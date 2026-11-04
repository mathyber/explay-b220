package javax.microedition.lcdui;

import java.util.Vector;

public abstract class Displayable {
    static final int BAR = 18;

    Display display;
    final Vector commands = new Vector();
    CommandListener listener;
    String title;
    boolean fullScreen;

    Displayable() {}

    public void addCommand(Command c) {
        if (c == null) throw new NullPointerException();
        if (!commands.contains(c)) commands.addElement(c);
        go1Changed();
    }

    public void removeCommand(Command c) {
        commands.removeElement(c);
        go1Changed();
    }

    public void setCommandListener(CommandListener l) {
        listener = l;
    }

    public String getTitle() { return title; }
    public void setTitle(String s) { title = s; go1Changed(); }
    public Ticker getTicker() { return null; }
    public void setTicker(Ticker t) {}

    public boolean isShown() {
        return display != null && display.getCurrent() == this;
    }

    public int getWidth() {
        return Display.WIDTH;
    }

    public int getHeight() {
        return fullScreen || commands.isEmpty() ? Display.HEIGHT : Display.HEIGHT - BAR;
    }

    protected void sizeChanged(int w, int h) {}

    void go1Changed() {
        if (isShown()) display.requestRepaint(0, 0, Display.WIDTH, Display.HEIGHT);
    }

    /* Hooks used by Display and other lcdui packages. */
    public void go1Show() {}
    public void go1Hide() {}
    public void go1Paint(Graphics g) {
        paintCommands(g);
    }

    Command commandFor(boolean right) {
        Command found = null;
        for (int i = 0; i < commands.size(); i++) {
            Command c = (Command) commands.elementAt(i);
            if (c.isNegative() == right && (found == null || c.getPriority() < found.getPriority())) found = c;
        }
        if (found == null && !right && commands.size() > 0) {
            for (int i = 0; i < commands.size(); i++) {
                Command c = (Command) commands.elementAt(i);
                if (commandFor(true) != c) return c;
            }
        }
        return found;
    }

    /* Soft keys run commands; returns true when the key was taken. */
    boolean softKey(int type, int code) {
        if (code != -6 && code != -7) return false;
        Command c = commandFor(code == -7);
        if (c == null) return false;
        if (type == go1.Events.KEY_PRESSED && listener != null) listener.commandAction(c, this);
        return true;
    }

    public void go1Key(int type, int code) {
        softKey(type, code);
    }

    void paintCommands(Graphics g) {
        if (fullScreen || commands.isEmpty()) return;
        int top = Display.HEIGHT - BAR;
        g.setClip(0, top, Display.WIDTH, BAR);
        g.setColor(0x202020);
        g.fillRect(0, top, Display.WIDTH, BAR);
        g.setColor(0xffffff);
        g.setFont(Font.getFont(Font.FACE_SYSTEM, Font.STYLE_PLAIN, Font.SIZE_SMALL));
        Command left = commandFor(false), right = commandFor(true);
        if (left != null) g.drawString(left.getLabel(), 2, Display.HEIGHT - 2, Graphics.LEFT | Graphics.BOTTOM);
        if (right != null && right != left) g.drawString(right.getLabel(), Display.WIDTH - 2, Display.HEIGHT - 2, Graphics.RIGHT | Graphics.BOTTOM);
    }
}
