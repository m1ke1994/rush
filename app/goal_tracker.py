from dataclasses import dataclass

def score_text(score): return f"{score[0]}:{score[1]}"

@dataclass
class Observation:
    score: tuple[int, int]
    half: str = "UNKNOWN"
    game_time: str | None = None

class GoalTracker:
    """Conservative score transition interpreter: it never invents goal order."""
    def __init__(self, initial=(0, 0)):
        self.previous = initial

    def transition(self, current: Observation):
        old, new = self.previous, current.score
        self.previous = new
        if new == old: return []
        da, db = new[0]-old[0], new[1]-old[1]
        if da < 0 or db < 0:
            return [("SCORE_CORRECTION", None, "UNKNOWN", old)]
        total = da + db
        if total != 1:
            return [("GAP", None, "UNKNOWN", old)]
        team = 1 if da else 2
        return [("GOAL", team, "HIGH", old)]
