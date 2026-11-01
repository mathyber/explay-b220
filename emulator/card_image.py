"""Exclusive host-side image ownership and sector writes, separate from SPI."""
import os
from pathlib import Path


class CardImage:
    def __init__(self,path):
        self.path = Path(path)
        self.stream = self.path.open('r+b')
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.data = self.stream.read()
            if len(self.data) not in (16*1024*1024,32*1024*1024):
                raise ValueError('SD image must be 16 or 32 MiB')
        except BaseException:
            self.stream.close()
            raise

    def flush(self,card):
        if len(card.image) != len(self.data):
            raise ValueError('SD image size changed')
        if not card.dirty_sectors:
            return
        for sector in sorted(card.dirty_sectors):
            self.stream.seek(sector*512)
            self.stream.write(card.image[sector*512:(sector+1)*512])
        self.stream.flush()
        os.fsync(self.stream.fileno())
        card.dirty_sectors.clear()

    def replace_contents(self,data):
        if len(data) != len(self.data):
            raise ValueError('SD image size changed')
        self.stream.seek(0)
        self.stream.write(data)
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.data = bytes(data)

    def close(self):
        self.stream.close()  # Closing also releases the OS lock.

    def __enter__(self):
        return self

    def __exit__(self,*_):
        self.close()
