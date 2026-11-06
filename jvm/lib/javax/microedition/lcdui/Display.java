package javax.microedition.lcdui;

import go1.Draw;
import go1.Events;
import go1.Launcher;
import java.util.Vector;
import javax.microedition.midlet.MIDlet;

/* The single display; its event loop runs on the launcher thread. */
public class Display {
    public static final int WIDTH = 176;
    public static final int HEIGHT = 220;
    public static final int COLOR_BACKGROUND = 0;
    public static final int COLOR_FOREGROUND = 1;
    public static final int COLOR_HIGHLIGHTED_BACKGROUND = 2;
    public static final int COLOR_HIGHLIGHTED_FOREGROUND = 3;
    public static final int COLOR_BORDER = 4;
    public static final int COLOR_HIGHLIGHTED_BORDER = 5;

    private static Display instance;
    private Displayable current;
    private final Vector serial = new Vector();
    private Thread eventThread;
    private boolean repaintPending, wakePosted;
    private int x0, y0, x1, y1;

    private Display() {}

    public static Display getDisplay(MIDlet midlet) {
        if (instance == null) instance = new Display();
        return instance;
    }

    public Displayable getCurrent() {
        return current;
    }

    public void setCurrent(Displayable next) {
        if (next == null || next == current) return;
        Displayable previous = current;
        current = next;
        next.display = this;
        if (previous != null) previous.go1Hide();
        next.go1Show();
        requestRepaint(0, 0, WIDTH, HEIGHT);
    }

    public void setCurrent(Alert alert, Displayable next) {
        alert.next = next;
        setCurrent(alert);
    }

    public void callSerially(Runnable r) {
        synchronized (serial) {
            serial.addElement(r);
        }
        wake();
    }

    public boolean isColor() { return true; }
    public int numColors() { return 65536; }
    public int numAlphaLevels() { return 256; }
    public boolean vibrate(int duration) { return false; }
    public boolean flashBacklight(int duration) { return false; }
    public int getColor(int specifier) { return specifier == COLOR_BACKGROUND || specifier == COLOR_HIGHLIGHTED_FOREGROUND ? 0xffffff : 0x000000; }
    public int getBestImageWidth(int type) { return 32; }
    public int getBestImageHeight(int type) { return 32; }

    private void wake() {
        synchronized (this) {
            if (wakePosted) return;
            wakePosted = true;
        }
        Events.post(Events.REPAINT, 0);
    }

    synchronized void requestRepaint(int x, int y, int w, int h) {
        if (w <= 0 || h <= 0) return;
        if (!repaintPending) {
            x0 = x; y0 = y; x1 = x + w; y1 = y + h;
            repaintPending = true;
        } else {
            x0 = Math.min(x0, x); y0 = Math.min(y0, y);
            x1 = Math.max(x1, x + w); y1 = Math.max(y1, y + h);
        }
        if (Thread.currentThread() != eventThread) wake();
    }

    void serviceRepaints() {
        if (Thread.currentThread() == eventThread) {
            paintPending();
            return;
        }
        while (repaintPending && !Launcher.isExiting()) {
            try {
                Thread.sleep(5);
            } catch (InterruptedException e) {
                return;
            }
        }
    }

    private void paintPending() {
        int px, py, pw, ph;
        synchronized (this) {
            if (!repaintPending) return;
            repaintPending = false;
            px = Math.max(0, x0); py = Math.max(0, y0);
            pw = Math.min(WIDTH, x1) - px; ph = Math.min(HEIGHT, y1) - py;
        }
        Displayable d = current;
        if (d == null || pw <= 0 || ph <= 0) return;
        Graphics g = new Graphics(null, WIDTH, HEIGHT);
        g.setClip(px, py, pw, ph);
        try {
            d.go1Paint(g);
        } catch (RuntimeException e) {
            e.printStackTrace();
        }
        Draw.flush(px, py, pw, ph);
    }

    private void runSerial() {
        while (true) {
            Runnable r;
            synchronized (serial) {
                if (serial.isEmpty()) return;
                r = (Runnable) serial.elementAt(0);
                serial.removeElementAt(0);
            }
            try {
                r.run();
            } catch (RuntimeException e) {
                e.printStackTrace();
            }
        }
    }

    /* Event loop: keys, serial calls and coalesced repaints, until notifyDestroyed. */
    public void run() {
        eventThread = Thread.currentThread();
        int[] event = new int[2];
        while (!Launcher.isExiting()) {
            if (Events.poll(event)) {
                if (event[0] == Events.EXIT) {
                    Launcher.platformExit();
                } else if (event[0] == Events.REPAINT) {
                    synchronized (this) {
                        wakePosted = false;
                    }
                    if (event[1] == 1) requestRepaint(0, 0, WIDTH, HEIGHT);  /* the platform lost the screen */
                } else if (current != null) {
                    try {
                        current.go1Key(event[0], event[1]);
                    } catch (RuntimeException e) {
                        e.printStackTrace();
                    }
                }
            }
            runSerial();
            paintPending();
        }
    }
}
