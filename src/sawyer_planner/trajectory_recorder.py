#!/usr/bin/env python

import openravepy
import os

import rospy
import rospkg
from geometry_msgs.msg import Pose
from geometry_msgs.msg import PoseStamped
from sensor_msgs.msg import JointState
from std_msgs.msg import Header
from std_srvs.srv import Empty
from sawyer_planner.srv import LoadTrajectory, AppendJoints, AppendPose
from online_planner.srv import *

from rgb_segmentation.srv import *
import cPickle

import datetime


class TrajectoryConfig(object):

    def __init__(self, name):
        self.name = name
        self.waypoints = []
        self.trajectories = []
        self.current_waypoint = 0

        self.plan_pose_client = rospy.ServiceProxy("/plan_pose_srv", PlanPose)
        self.plan_joints_client = rospy.ServiceProxy("/plan_joints_srv", PlanJoints)
        self.execute_traj_client = rospy.ServiceProxy("/execute_traj_srv", ExecuteTraj)

        # Services - Cannot create more than one object - NS eventually?
        self.save_srv = rospy.ServiceProxy("traj_recorder/save_current_traj", Empty, self.save)
        self.load_srv = rospy.ServiceProxy("traj_recorder/load_traj", LoadTrajectory, self.load)
        self.append_joints_srv = rospy.ServiceProxy("traj_recorder/append_joints", AppendJoints, self.append_joints)
        self.append_pose_srv = rospy.ServiceProxy("traj_recorder/append_pose", AppendPose, self.append_pose)
        self.plan_srv = rospy.ServiceProxy("traj_recorder/plan_all", Empty, self.plan_all)
        self.playback_srv = rospy.ServiceProxy("traj_recorder/playback", Empty, self.playback)
        self.plan_to_start_srv = rospy.ServiceProxy("traj_recorder/plan_to_start", Empty, self.plan_to_start)

    @classmethod
    def get_file_path(cls, name):
        rospack = rospkg.RosPack()
        file_path = os.path.join(rospack.get_path('sawyer_planner'), 'configs', '{}.config'.format(name))
        return file_path

    def load(self, name, *_, **__):
        if not isinstance(name, str):   # Is a ROS message
            name = name.name.data
        name.replace('.config', '')
        file_path = self.get_file_path(name)
        with open(file_path, 'rb') as fh:
            config = cPickle.load(fh)

        self.name = name
        self.waypoints = config['waypoints']
        self.trajectories = config['trajectories']

        return []

    def save(self, *_, **__):
        file_path = self.get_file_path(self.name)
        to_save = {
            'waypoints': self.waypoints,
            'trajectories': self.trajectories
        }
        with open(file_path, 'wb') as fh:
            cPickle.dump(to_save, fh)

        print('Config file saved to {}'.format(file_path))

        return []

    def append_joints(self, append_msg):
        joints = append_msg.joints
        if not len(joints.positions):
            joints = self.construct_joint_message()
        self.waypoints.append(joints)

        return []

    def append_pose(self, append_msg):
        if not self.waypoints:
            raise Exception('First point of a trajectory must be a joint configuration, not a pose!')
        pose = append_msg.pose
        if not any([pose.position.x, pose.position.y, pose.position.z]):
            pose = rospy.wait_for_message('/manipulator_pose', PoseStamped, 1.0).pose
        self.waypoints.append(pose)

        return []


    @staticmethod
    def construct_joint_message(*angles):
        msg = rospy.wait_for_message('/manipulator_joints', JointState, 1.0)
        msg.header = Header()
        if len(angles):
            msg.position = angles

        return msg

    @staticmethod
    def construct_pose_lookat(source, target, up_axis=None):

        if up_axis is None:
            up_axis = [0, 0, -1]

        goal_off_pose_mat = openravepy.transformLookat(target, source, up_axis)
        goal_off_pose = openravepy.poseFromMatrix(goal_off_pose_mat)

        return or_pose_to_ros_msg(goal_off_pose)

    def plan(self, waypoint_index):

        start = self.waypoints[waypoint_index]
        if not isinstance(start, JointState):
            start = JointState()
            previous_traj = self.trajectories[(waypoint_index - 1) % len(self.waypoints)]
            start.name = previous_traj.joint_names
            start.position = previous_traj.points[-1].positions

        goal = self.waypoints[(waypoint_index + 1) % len(self.waypoints)]
        if isinstance(goal, Pose):
            resp = self.plan_pose_client(goal, False, False, start)
        else:
            resp = self.plan_joints_client(goal, False, False, start)

        traj = resp.traj
        self.trajectories.append(traj)

    def plan_to_start(self, *_, **__):
        self.plan_joints_client(self.waypoints[0], False, True, JointState())
        self.current_waypoint = 0
        return []

    def plan_all(self, *_, **__):
        self.trajectories = []
        for i in range(len(self.waypoints)):
            self.plan(i)

        return []


    def execute(self, waypoint_index=None, set_start=False):
        if waypoint_index is None:
            waypoint_index = self.current_waypoint

        traj = self.trajectories[waypoint_index]
        self.execute_traj_client(traj, False, set_start)

        self.current_waypoint = (waypoint_index + 1) % len(self.waypoints)

        return []

    def playback(self):
        for i in range(len(self.waypoints)):
            self.execute(i, set_start=True)
            rospy.sleep(0.5)





def or_pose_to_ros_msg(pose):
    pose_msg = Pose()
    pose_msg.orientation.w = pose[0]
    pose_msg.orientation.x = pose[1]
    pose_msg.orientation.y = pose[2]
    pose_msg.orientation.z = pose[3]
    pose_msg.position.x = pose[4]
    pose_msg.position.y = pose[5]
    pose_msg.position.z = pose[6]
    return pose_msg







if __name__ == '__main__':

    rospy.init_node('trajectory_playback')

    traj = TrajectoryConfig(datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    mode = ''
    if len(sys.argv) > 1:
        mode = sys.argv[1]

    if mode == 'wizard':

        def input_to_array(prompt):
            input_str = raw_input(prompt).strip()
            if not input_str:
                return []
            return [float(x.strip()) for x in input_str.split(',')]

        while True:
            print('What would you like to do?')
            action = raw_input('j: Add joints\np: Add pose\ns: Save\nl: Load\nv: View\nplan: Plan\nplay: Playback\ne: Execute next\nstart: Move to start\nq: Quit\n').strip()
            if action == 'j':
                print('Enter joints as comma-separated values, or leave empty to use current')
                joints = input_to_array('Joints: ')
                traj.waypoints.append(traj.construct_joint_message(*joints))

            elif action == 'p':
                print("What's the look-at point?")
                pt_1 = input_to_array('Point 1: ')

                print("What's the source point?")
                pt_2 = input_to_array('Point 2: ')

                traj.waypoints.append(traj.construct_pose_lookat(pt_2, pt_1))

            elif action == 's':
                traj.save()

            elif action == 'l':
                to_load = raw_input('Config to load: ').strip()
                traj.load(to_load)
            elif action == 'v':
                print('Current number of waypoints: {}'.format(len(traj.waypoints)))
                print('Current number of computed trajectories: {}'.format(len(traj.trajectories)))
                print('Current status: {}'.format(traj.current_waypoint))
            elif action == 'plan':
                traj.plan_all()

            elif action == 'play':
                traj.playback()
            elif action == 'e':
                traj.execute()
            elif action == 'start':
                traj.plan_to_start()
            elif action == 'q':
                break
            else:
                print('Unknown action {}'.format(action))
                continue

    else:
        rospy.spin()















