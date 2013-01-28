import argparse
import sys
from lfd import multi_item_verbs, scene_diff
import os
import os.path as osp
import numpy as np

# script that uses scene_diff to find the closest demo to each of the test demos

BASE_DATA_DIR = "multi_item/empty_move_data"

# finds the closest scene to a single demo
def get_closest_single_scene(data_dir, demo_base_name):
    verb_data_accessor = multi_item_verbs.VerbDataAccessor(test_info_dir=osp.join("test", BASE_DATA_DIR, data_dir))
    abs_exp_dir = osp.join(osp.dirname(osp.abspath(__file__)), "exp_pcs")
    possible_demo_names = get_possible_demo_names(demo_base_name, 2)
    # assuming that the first possible_demo_name is a demo
    verb = verb_data_accessor.get_verb_from_demo_name(possible_demo_names[0])
    verb_pc_name_dict = get_exp_pcs_for_verb(verb)
    exp_clouds = []
    for pc_file in verb_pc_name_dict[demo_base_name]:
        exp_clouds.append(np.loadtxt(osp.join(abs_exp_dir, pc_file)))
    scene_diff_closest_name = scene_diff.get_closest_demo(verb_data_accessor, verb, exp_clouds, ignore=possible_demo_names)
    return scene_diff_closest_name

def get_possible_demo_names(demo_base_name, n=2):
    return ["%s%i" % (demo_base_name, i) for i in xrange(n)]

def get_exp_pcs_for_verb(verb):
    abs_exp_dir = osp.join(osp.dirname(osp.abspath(__file__)), "exp_pcs")
    all_exp_pcs = os.listdir(abs_exp_dir)
    verb_exp_pcs = [pc for pc in all_exp_pcs if pc.find(verb) == 0]
    verb_pc_name_dict = {}
    for verb_pc_name in verb_exp_pcs:
        demo_base_name = verb_pc_name.split(".")[0]
        if not verb_pc_name_dict.has_key(demo_base_name):
            verb_pc_name_dict[demo_base_name] = []
        verb_pc_name_dict[demo_base_name].append(verb_pc_name)
    for demo_base_name in verb_pc_name_dict.keys():
        verb_pc_name_dict[demo_base_name].sort()
    return verb_pc_name_dict

# finds closest scenes for all demos for verb in data directory
def get_closest_scenes(data_dir, verb):
    verb_data_accessor = multi_item_verbs.VerbDataAccessor(test_info_dir=osp.join("test", BASE_DATA_DIR, data_dir))
    abs_exp_dir = osp.join(osp.dirname(osp.abspath(__file__)), "exp_pcs")
    verb_pc_name_dict = get_exp_pcs_for_verb(verb)
    closest = {}
    for demo_base_name in verb_pc_name_dict.keys():
        exp_clouds = []
        for pc_file in verb_pc_name_dict[demo_base_name]:
            exp_clouds.append(np.loadtxt(osp.join(abs_exp_dir, pc_file)))
        possible_demo_names = get_possible_demo_names(demo_base_name, 2)
        scene_diff_closest_name = scene_diff.get_closest_demo(verb_data_accessor, verb, exp_clouds, ignore=possible_demo_names)
        closest[demo_name] = scene_diff_closest_name
    return closest

def print_usage():
    print "Arguments:"
    print "--dir: look at data in this directory (must always be specified)"
    print "--verb: find closest scenes for tasks with this verb" 
    print "--demo: find closest scene for this demo"

def results_for_data_dir_as_str(data_dir, closest):
    results = "%s:\n" % data_dir
    for demo_name, closest_demo_name in closest.items():
        results += "%s is closest to %s\n" % (demo_name, closest_demo_name)
    return results

def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir")
    parser.add_argument("--verb")
    parser.add_argument("--demo")
    args = parser.parse_args()
    return args

if __name__ == "__main__":
    args = get_args()
    if (sys.argv) == 0:
        print_usage()
    else:
        if args.verb: # find closest scenes for a certain test directory
            closest = get_closest_scenes(args.dir, args.verb)
            results = results_for_data_dir_as_str(args.dir, closest)
        elif args.demo: # find closest scenes for a single demo
            closest_name = get_closest_single_scene(args.dir, args.demo)
            results = "%s:\n%s is closest to %s\n" % (args.dir, args.demo, closest_name)

    print
    print "##### RESULTS #####"
    print
    print results
