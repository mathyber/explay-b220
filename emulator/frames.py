"""Optional host preview output must not interrupt the emulated device."""
from pathlib import Path


class LiveFramePublisher:
    def __init__(self,path):
        self.path = Path(path)
        self.published = 0
        self.skipped = 0
        self.last_error = None

    def __call__(self,lcd):
        try:
            path = lcd.save(self.path.parent,self.path.stem,write_raw=False)
        except OSError as exc:
            # Only presentation I/O is optional. Guest/model exceptions still
            # propagate; the final frame is saved independently after the run.
            self.skipped += 1
            self.last_error = str(exc)
        else:
            if path:
                self.published += 1

    def report(self):
        return dict(path=str(self.path),published=self.published,skipped=self.skipped,
                    last_error=self.last_error)
