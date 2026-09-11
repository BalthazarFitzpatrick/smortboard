"""a tick starts cards outside the scheduler's state lock, then writes its leftover queue back - so
a stop() or a start_all() landing mid-tick must wait for it, not be overwritten by it"""

import threading
from types import SimpleNamespace

from smortboard.scheduler import BoardScheduler
from smortboard.store.api import Store


class GatedRuns:
    """holds every .start() open until the test releases it, the way a slow thread start or a
    store read would hold a real tick open"""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def start(self, card_id, runner=None, on_finish=None):
        self.entered.set()
        self.release.wait(5)
        return SimpleNamespace(card_id=card_id, running=True)


def _board_with_todo_cards(db_path, count):
    with Store(db_path) as store:
        board = store.create_board("b")
        repo = store.create_repo(board["id"], "r", "/tmp/r", "main")
        for i in range(count):
            store.create_card(board["id"], repo["id"], f"card {i}")
        store.set_setting("max_parallel", "1")
        return board["id"]


def test_a_stop_during_a_tick_is_not_undone_by_it(tmp_path):
    db_path = tmp_path / "board.db"
    board_id = _board_with_todo_cards(db_path, 3)
    runs = GatedRuns()
    scheduler = BoardScheduler(board_id, db_path, runs)

    starter = threading.Thread(target=scheduler.start_all)
    starter.start()
    assert runs.entered.wait(5), "the tick never reached runs.start"
    stopper = threading.Thread(target=scheduler.stop)
    stopper.start()
    stopper.join(0.3)  # unguarded, the stop has already cleared the queue by now
    runs.release.set()
    starter.join(5)
    stopper.join(5)

    assert scheduler.schedule_view()["queued"] == []
