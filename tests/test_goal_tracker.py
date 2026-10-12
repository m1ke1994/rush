from app.goal_tracker import GoalTracker, Observation

def test_single_goal_is_known():
    t=GoalTracker((0,0)); assert t.transition(Observation((1,0))) == [("GOAL",1,"HIGH",(0,0))]
def test_jump_is_marked_gap_not_invented():
    t=GoalTracker((1,0)); assert t.transition(Observation((3,0))) == [("GAP",None,"UNKNOWN",(1,0))]
def test_two_sides_changed_is_unknown():
    t=GoalTracker((0,0)); assert t.transition(Observation((1,1))) == [("GAP",None,"UNKNOWN",(0,0))]
