
from brett2.ros_utils import RvizWrapper,Marker,pc2xyzrgb,xyz2pc
import brett2.ros_utils as ru
import h5py, rospy
import lfd
import os.path as osp
from lfd import warping, registration
import roslib
roslib.load_manifest("verb_msgs")
from verb_msgs.srv import MakeTrajectoryRequest, MakeTrajectoryResponse, MakeTrajectory
import geometry_msgs.msg as gm
from numpy import asarray
import numpy as np
from jds_utils import conversions
import yaml
from jds_image_proc.clouds import voxel_downsample
from lfd import verbs, tps
from jds_utils.conversions import quats2mats, mats2quats, mat2quat

import jds_utils.transformations as jut
import jds_utils.conversions as juc
from registration import orthogonalize3_cross
from utils_lfd import group_to_dict

#with open(osp.join(osp.dirname(lfd.__file__),"data","tool_info.yaml"),"r") as fh:
    #tool_info = yaml.load(fh)

class Globals:
    handles = []
    rviz = None
    @staticmethod
    def setup():
        Globals.rviz = RvizWrapper.create()

def xyzs_quats_to_poses(xyzs, quats):
    poses = []
    for (xyz, quat) in zip(xyzs, quats):
        poses.append(gm.Pose(gm.Point(*xyz), gm.Quaternion(*quat)))
    return poses

def make_traj(req):
    """
    Generate a trajectory using warping
    See MakeTrajectory service description
    (TODO) should be able to specify a specific demo
    """
    assert isinstance(req, MakeTrajectoryRequest)
    
    new_object_clouds = [pc2xyzrgb(cloud)[0] for cloud in req.object_clouds]
    
    scene_info = "PLACEHOLDER"
    best_demo_name, best_demo_info = verbs.get_closest_demo(req.verb, scene_info)    
    best_demo_data = verbs.get_demo_data(best_demo_name)
        
    transform_type = "tps"
    
    old_object_clouds = [best_demo_data["object_clouds"][obj_name]["xyz"]
            for obj_name in best_demo_data["object_clouds"].keys()]
    
    if len(old_object_clouds) > 1:
        raise Exception("i don't know what to do with multiple object clouds")
    x_nd = voxel_downsample(old_object_clouds[0],.02)
    y_md = voxel_downsample(new_object_clouds[0],.02)
    
    if transform_type == "tps":
        #warp = registration.tps_rpm_zrot(x_nd, y_md, plotting=2,reg_init=2,reg_final=.05, n_iter=10, verbose=False)
        warp = registration.tps_rpm(x_nd, y_md, plotting=2,reg_init=2,reg_final=.05, n_iter=10, verbose=False)
    elif transform_type == "translation2d":
        warp = registration.Translation2d()
        warp.fit(x_nd, y_md)
    elif transform_type == "rigid2d":
        warp = registration.Rigid2d()
        warp.fit(x_nd, y_md)
    else:
        raise Exception("transform type %s is not yet implemented"%transform_type)        

    l_offset,r_offset = np.zeros(3), np.zeros(3)
    #if "r_tool" in best_demo_info:
        #r_offset = asarray(tool_info[this_verb_info["r_tool"]]["translation"])
    #if "l_tool" in best_demo_info:
        #l_offset = asarray(tool_info[this_verb_info["l_tool"]]["translation"])


    arms_used = best_demo_info["arms_used"]
    warped_demo_data = warping.transform_verb_demo(warp, best_demo_data)        

    resp = MakeTrajectoryResponse()
    traj = resp.traj

    for i in xrange(10):
        print warped_demo_data["r_gripper_tool_frame"]["position"][i]
        print warped_demo_data["r_gripper_tool_frame"]["orientation"][i]
        
    traj.arms_used = arms_used
    if arms_used in "lb":        
        traj.l_gripper_poses.poses = xyzs_quats_to_poses(warped_demo_data["l_gripper_tool_frame"]["position"], warped_demo_data["l_gripper_tool_frame"]["orientation"])
        traj.l_gripper_angles = warped_demo_data["l_gripper_joint"]
        traj.l_gripper_poses.header.frame_id = req.object_clouds[0].header.frame_id
        if "l_tool" in best_demo_info: traj.l_gripper_angles *= 0
    if arms_used in "rb":
        traj.r_gripper_poses.poses = xyzs_quats_to_poses(warped_demo_data["r_gripper_tool_frame"]["position"], warped_demo_data["r_gripper_tool_frame"]["orientation"])
        traj.r_gripper_angles = warped_demo_data["r_gripper_joint"]
        traj.r_gripper_poses.header.frame_id = req.object_clouds[0].header.frame_id
        if "r_tool" in best_demo_info: traj.r_gripper_angles *= 0
        

    Globals.handles = []
    plot_original_and_warped_demo(best_demo_data, warped_demo_data, traj)
    

    pose_array = conversions.array_to_pose_array(y_md, 'base_footprint')
    Globals.handles.append(Globals.rviz.draw_curve(pose_array, rgba = (0,0,1,1),width=.01,type=Marker.CUBE_LIST))
    return resp

def get_tps_transform(old_object_cloud, new_object_cloud):
    return registration.tps_rpm(old_object_cloud, new_object_cloud)
    
# get the transformation from old_object_cloud to new_object_cloud linearized at lin_point (3-element vector) and made into a rigid transformation (orthogonalized)
def lin_rigid_tps_transform(tps_transform, lin_point):
    lin_ag, trans_g, w_ng, x_na = tps_transform.lin_ag, tps_transform.trans_g, tps_transform.w_ng, tps_transform.x_na
    trans = tps.tps_eval(np.array([lin_point]), lin_ag, trans_g, w_ng, x_na)[0]
    print "trans ", trans
    orien = juc.mat2quat(tps.tps_grad(np.array([lin_point]), lin_ag, trans_g, w_ng, x_na)[0])
    linearized_transform = juc.trans_rot_to_hmat(trans, orien)
    return orthogonalize3_cross(linearized_transform)

def get_homog_coord(point):
    homog = np.ones(4)
    homog[:3] = point
    return homog

def get_array3(homog):
    return homog[:3]
    
# transform is a 4x4 matrix (np.array) and point is a 3-element vector (np.array)
def apply_transform(transform, point):
    applied = np.dot(transform, get_homog_coord(point))
    return get_array3(applied)
    
# make trajectory for a certain stage of a task
# current_stage_info is the demo information to use
# stage_num is the current task stage number; previous information is unused if this is zero
# prev_exp_clouds has the point cloud of the object from the previous stage in the gripper frame
# 'prev' and 'cur' is for the previous and current stages; 'demo' and 'new' are for demonstration and new experiment situations, respectively
def make_traj_multi_stage(req, current_stage_info, stage_num, prev_stage_info, prev_exp_clouds):

    assert isinstance(req, MakeTrajectoryRequest)

    verb_stage_data = verbs.get_demo_data(current_stage_info.stage_name)

    # get current transformation from base to gripper
    # (prev_exp_base_to_gripper_trans, prev_exp_base_to_gripper_rot) = get_transform("base_footprint", "r_gripper")
    # prev_exp_base_to_gripper_transform = juc.trans_rot_to_hmat(prev_exp_base_to_gripper_trans, prev_exp_base_to_gripper_rot)

    if stage_num == 0:
        # don't do any extra transformation for the first stage
        prev_exp_to_demo_grip_transform_lin_rigid = np.identity(4)
        # no special point translation for first stage since no tool yet
        special_point_translation = np.identity(4)
    elif stage_num > 0:
        prev_stage_data = verbs.get_demo_data(prev_stage_info.stage_name)
        prev_demo_pc = prev_stage_data["object_clouds"][prev_stage_info.item]["xyz"]
        prev_exp_pc = [pc2xyzrgb(cloud)[0] for cloud in prev_exp_clouds[0]]
        prev_demo_pc_down = voxel_downsample(prev_demo_pc, .02)
        prev_exp_pc_down = voxel_downsample(prev_exp_pc, .02)

        # transform point cloud in base frame to gripper frame
        # assume right hand has the tool for now
        # use the last pose of the gripper in the stage to figure out the point cloud of the tool in the gripper frame when the tool was grabbed
        prev_demo_gripper_pos = prev_stage_data["r_gripper_tool_frame"]["position"][-1]
        prev_demo_gripper_orien = prev_stage_data["r_gripper_tool_frame"]["orientation"][-1]
        prev_demo_gripper_to_base_transform = juc.trans_rot_to_hmat(prev_demo_gripper_pos, prev_demo_gripper_orien)
        prev_demo_base_to_gripper_transform = np.linalg.inv(prev_demo_gripper_to_base_transform)
        prev_demo_pc_in_gripper_frame = [apply_transform(prev_demo_base_to_gripper_transform, point) for point in prev_demo_pc_down]

        # get the new point cloud in the new gripper frame
        # prev_exp_pc_in_gripper_frame = [apply_transform(prev_exp_base_to_gripper_transform, point) for point in prev_exp_pc_down]
        prev_exp_pc_in_gripper_frame = ru.transform_points(prev_exp_pc_down, ru.get_tf_listener(), "base_footprint", "r_gripper")

        # get the transformation from the new point cloud to the old point cloud for the previous stage
        prev_exp_to_demo_grip_transform = get_tps_transform(prev_exp_pc_in_gripper_frame, prev_demo_pc_in_gripper_frame)

        # transforms gripper trajectory point into special point trajectory point
        if prev_stage_data.special_point is None:
            # if there is no special point, linearize at origin
            prev_exp_to_demo_grip_transform_lin_rigid = lin_rigid_tps_transform(prev_exp_to_demo_grip_transform, [0, 0, 0])
            # don't do a special point translation if there is no specified special point
            special_point_translation = np.identity(4)
        else:
            prev_exp_to_demo_grip_transform_lin_rigid = lin_rigid_tps_transform(prev_exp_to_demo_grip_transform, prev_stage_data.special_point)
            special_point_translation = translation_matrix(np.array(prev_stage_data.special_point))

    # find the special point trajectory before the target transformation
    cur_demo_gripper_traj_xyzs = verb_stage_data["r_gripper_tool_frame"]["position"]
    cur_demo_gripper_traj_orien = verb_stage_data["r_gripper_tool_frame"]["orientation"]
    cur_demo_gripper_traj_mats = [juc.trans_rot_to_hmat(trans, orien) for (trans, orien) in zip(cur_demo_gripper_traj_xyzs, cur_demo_gripper_traj_orien)]
    cur_demo_spec_pt_traj_mats = [np.dot(np.dot(gripper_mat, prev_exp_to_demo_grip_transform_lin_rigid), special_point_translation) for gripper_mat in cur_demo_gripper_traj_mats]

    # find the target transformation for the experiment scene
    demo_object_clouds = [verb_stage_data["object_clouds"][obj_name]["xyz"] for obj_name in verb_stage_data["object_clouds"].keys()]
    if len(demo_object_clouds) > 1:
        raise Exception("i don't know what to do with multiple object clouds")
    exp_object_clouds = [pc2xyzrgb(cloud)[0] for cloud in req.object_clouds]
    x_nd = voxel_downsample(demo_object_clouds[0], .02)
    y_md = voxel_downsample(exp_object_clouds[0], .02)
    # transformation from old target object to new target object in world frame
    cur_demo_to_exp_transform = get_tps_transform(x_nd, y_md)

    cur_exp_spec_pt_traj_mats = []
    for spec_pt_traj_xyz, spec_pt_traj_mat in zip(cur_demo_spec_pt_traj_xyzs, cur_demo_spec_pt_traj_mats):
        # get the special point trajectory; if there is no special point, then the special_point_translation will just be the identity
        cur_demo_to_exp_transform_lin_rigid = lin_rigid_tps_transform(cur_demo_to_exp_transform, special_point_xyz)
        cur_exp_spec_pt_traj_mats.append(np.dot(np.dot(cur_demo_to_exp_transform_lin_rigid, spec_pt_traj_mat), prev_exp_to_demo_grip_transform_lin_rigid))

    # assume only right arm is used for now
    #arms_used = current_stage_info.arms_used
    arms_used = 'r'

    warped_stage_data = group_to_dict(verb_stage_data) # deep copy it
    warped_stage_data["r_gripper_tool_frame"]["position"] = []
    warped_stage_data["r_gripper_tool_frame"]["orientation"] = []
    for exp_traj_mat in cur_exp_gripper_traj_mats:
        warped_pos, warped_orien = juc.hmat_to_trans_rot(exp_traj_mat)
        warped_stage_data["r_gripper_tool_frame"]["position"].append(warped_pos)
        warped_stage_data["r_gripper_tool_frame"]["orientation"].append(warped_orien)
        #print "mat ", exp_traj_mat, "pos ", warped_pos, "orien ", warped_orien

    resp = MakeTrajectoryResponse()
    traj = resp.traj
        
    traj.arms_used = arms_used
    if arms_used in "rb":
        traj.r_gripper_poses.poses = xyzs_quats_to_poses(warped_stage_data["r_gripper_tool_frame"]["position"], warped_stage_data["r_gripper_tool_frame"]["orientation"])
        print "poses: ", len(traj.r_gripper_poses.poses)
        traj.r_gripper_angles = warped_stage_data["r_gripper_joint"]
        traj.r_gripper_poses.header.frame_id = req.object_clouds[0].header.frame_id
        
    Globals.handles = []
    plot_original_and_warped_demo(verb_stage_data, warped_stage_data, traj)
    
    pose_array = conversions.array_to_pose_array(y_md, 'base_footprint')
    Globals.handles.append(Globals.rviz.draw_curve(pose_array, rgba = (0,0,1,1),width=.01,type=Marker.CUBE_LIST))
    return resp

def plot_original_and_warped_demo(best_demo, warped_demo, traj):
    arms_used = best_demo["arms_used"]

    if arms_used in "lb":
        pose_array = conversions.array_to_pose_array(asarray(best_demo["l_gripper_tool_frame"]["position"]), 'base_footprint')
        Globals.handles.append(Globals.rviz.draw_curve(pose_array, rgba = (1,0,0,1),ns = "make_verb_traj_service"))
        pose_array = conversions.array_to_pose_array(asarray(warped_demo["l_gripper_tool_frame"]["position"]), 'base_footprint')
        Globals.handles.append(Globals.rviz.draw_curve(pose_array, rgba = (0,1,0,1),ns = "make_verb_traj_service"))
        
    if arms_used in "rb":
        pose_array = conversions.array_to_pose_array(asarray(best_demo["r_gripper_tool_frame"]["position"]), 'base_footprint')
        Globals.handles.append(Globals.rviz.draw_curve(pose_array, rgba = (1,0,0,1),ns = "make_verb_traj_service"))
        pose_array = conversions.array_to_pose_array(asarray(warped_demo["r_gripper_tool_frame"]["position"]), 'base_footprint')
        Globals.handles.append(Globals.rviz.draw_curve(pose_array, rgba = (0,1,0,1),ns = "make_verb_traj_service"))

    Globals.handles.extend(Globals.rviz.draw_trajectory(traj.l_gripper_poses, traj.l_gripper_angles, ns = "make_verb_traj_service_grippers"))
    if arms_used == 'b':
        Globals.handles.extend(Globals.rviz.draw_trajectory(traj.r_gripper_poses, traj.r_gripper_angles, ns = "make_verb_traj_service_grippers"))
        

    for (clouds,rgba) in [(sorted_values(best_demo["object_clouds"]),(1,0,0,.5)),
                          (sorted_values(warped_demo["object_clouds"]),(0,1,0,.5))]:

        cloud = []
        for subcloud in clouds:
            cloud.extend(np.asarray(subcloud["xyz"]).reshape(-1,3))
        cloud = np.array(cloud)
        
        cloud = voxel_downsample(cloud, .02)
        pose_array = conversions.array_to_pose_array(cloud, 'base_footprint')
        Globals.handles.append(Globals.rviz.draw_curve(pose_array, rgba = rgba,width=.01,type=Marker.CUBE_LIST))

def sorted_values(d):
    return [d[key] for key in sorted(d.keys())]
