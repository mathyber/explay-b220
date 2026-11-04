package go1;

import java.util.Vector;
import javax.microedition.media.Control;
import javax.microedition.media.MediaException;
import javax.microedition.media.Player;
import javax.microedition.media.PlayerListener;
import javax.microedition.media.control.VolumeControl;

/* A Player with states, media time and volume but no sound. */
public class SilentPlayer implements Player, VolumeControl {
    private final String type;
    private final Vector listeners = new Vector();
    private int state = UNREALIZED, level = 100;
    private boolean muted;
    private long started, elapsed;

    public SilentPlayer(String type) { this.type = type; }

    private void check() {
        if (state == CLOSED) throw new IllegalStateException();
    }

    private void tell(String event, Object data) {
        for (int i = 0; i < listeners.size(); i++) ((PlayerListener) listeners.elementAt(i)).playerUpdate(this, event, data);
    }

    public void realize() throws MediaException {
        check();
        if (state < REALIZED) state = REALIZED;
    }

    public void prefetch() throws MediaException {
        realize();
        if (state < PREFETCHED) state = PREFETCHED;
    }

    public void start() throws MediaException {
        prefetch();
        if (state == STARTED) return;
        state = STARTED;
        started = System.currentTimeMillis();
        tell(PlayerListener.STARTED, new Long(elapsed));
    }

    public void stop() throws MediaException {
        check();
        if (state != STARTED) return;
        elapsed = getMediaTime();
        state = PREFETCHED;
        tell(PlayerListener.STOPPED, new Long(elapsed));
    }

    public void deallocate() {
        check();
        if (state == STARTED) {
            elapsed = getMediaTime();
            state = PREFETCHED;
        }
        if (state == PREFETCHED) state = REALIZED;
    }

    public void close() {
        if (state == CLOSED) return;
        state = CLOSED;
        tell(PlayerListener.CLOSED, null);
    }

    public long setMediaTime(long now) throws MediaException {
        check();
        elapsed = now < 0 ? 0 : now;
        started = System.currentTimeMillis();
        return elapsed;
    }

    public long getMediaTime() {
        if (state == STARTED) return elapsed + (System.currentTimeMillis() - started) * 1000;
        return elapsed;
    }

    public int getState() { return state; }
    public long getDuration() { return TIME_UNKNOWN; }
    public String getContentType() { return type; }
    public void setLoopCount(int count) {
        if (count == 0) throw new IllegalArgumentException();
    }
    public void addPlayerListener(PlayerListener l) { if (l != null) listeners.addElement(l); }
    public void removePlayerListener(PlayerListener l) { listeners.removeElement(l); }

    public Control getControl(String name) {
        return name.equals("VolumeControl") || name.equals("javax.microedition.media.control.VolumeControl") ? this : null;
    }
    public Control[] getControls() { return new Control[] {this}; }

    public void setMute(boolean mute) { muted = mute; }
    public boolean isMuted() { return muted; }
    public int setLevel(int l) { level = l < 0 ? 0 : l > 100 ? 100 : l; return level; }
    public int getLevel() { return level; }
}
