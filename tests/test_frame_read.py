"""Frame seek helpers."""

import cv2

from matchref.frame_read import seek_to_frame


def test_msec_seek_uses_timeline_fps() -> None:
    class Cap:
        def __init__(self) -> None:
            self.msec = 0.0
            self.pos = 0

        def set(self, prop: float, value: float) -> None:
            if prop == cv2.CAP_PROP_POS_MSEC:
                self.msec = value
                self.pos = int((value / 1000.0) * 24.0)
            if prop == cv2.CAP_PROP_POS_FRAMES:
                self.pos = int(value)

        def get(self, prop: float) -> float:
            if prop == cv2.CAP_PROP_POS_FRAMES:
                return float(self.pos)
            return 0.0

        def grab(self) -> bool:
            self.pos += 1
            return self.pos <= 200

    cap = Cap()
    seek_to_frame(cap, 100, 24.0, mode="msec")
    assert cap.msec == (100 / 24.0) * 1000.0
