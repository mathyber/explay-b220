package javax.microedition.lcdui;

import b22.Events;

public abstract class Canvas extends Displayable {
    public static final int UP = 1;
    public static final int DOWN = 6;
    public static final int LEFT = 2;
    public static final int RIGHT = 5;
    public static final int FIRE = 8;
    public static final int GAME_A = 9;
    public static final int GAME_B = 10;
    public static final int GAME_C = 11;
    public static final int GAME_D = 12;
    public static final int KEY_NUM0 = 48;
    public static final int KEY_NUM1 = 49;
    public static final int KEY_NUM2 = 50;
    public static final int KEY_NUM3 = 51;
    public static final int KEY_NUM4 = 52;
    public static final int KEY_NUM5 = 53;
    public static final int KEY_NUM6 = 54;
    public static final int KEY_NUM7 = 55;
    public static final int KEY_NUM8 = 56;
    public static final int KEY_NUM9 = 57;
    public static final int KEY_STAR = 42;
    public static final int KEY_POUND = 35;

    protected Canvas() {}

    protected abstract void paint(Graphics g);

    public final void repaint() {
        repaint(0, 0, Display.WIDTH, Display.HEIGHT);
    }

    public final void repaint(int x, int y, int w, int h) {
        if (isShown()) display.requestRepaint(x, y, w, h);
    }

    public final void serviceRepaints() {
        if (isShown()) display.serviceRepaints();
    }

    protected void keyPressed(int keyCode) {}
    protected void keyReleased(int keyCode) {}
    protected void keyRepeated(int keyCode) {}
    protected void pointerPressed(int x, int y) {}
    protected void pointerReleased(int x, int y) {}
    protected void pointerDragged(int x, int y) {}
    protected void showNotify() {}
    protected void hideNotify() {}

    public boolean hasPointerEvents() { return false; }
    public boolean hasPointerMotionEvents() { return false; }
    public boolean hasRepeatEvents() { return true; }
    public boolean isDoubleBuffered() { return true; }

    public void setFullScreenMode(boolean mode) {
        if (fullScreen == mode) return;
        fullScreen = mode;
        sizeChanged(getWidth(), getHeight());
        repaint();
    }

    public int getGameAction(int keyCode) {
        switch (keyCode) {
            case -1: case KEY_NUM2: return UP;
            case -2: case KEY_NUM8: return DOWN;
            case -3: case KEY_NUM4: return LEFT;
            case -4: case KEY_NUM6: return RIGHT;
            case -5: case KEY_NUM5: return FIRE;
            case KEY_NUM1: return GAME_A;
            case KEY_NUM3: return GAME_B;
            case KEY_NUM7: return GAME_C;
            case KEY_NUM9: return GAME_D;
            default: return 0;
        }
    }

    public int getKeyCode(int gameAction) {
        switch (gameAction) {
            case UP: return -1;
            case DOWN: return -2;
            case LEFT: return -3;
            case RIGHT: return -4;
            case FIRE: return -5;
            case GAME_A: return KEY_NUM1;
            case GAME_B: return KEY_NUM3;
            case GAME_C: return KEY_NUM7;
            case GAME_D: return KEY_NUM9;
            default: throw new IllegalArgumentException();
        }
    }

    public String getKeyName(int keyCode) {
        if (keyCode >= KEY_NUM0 && keyCode <= KEY_NUM9 || keyCode == KEY_STAR || keyCode == KEY_POUND) return String.valueOf((char) keyCode);
        switch (keyCode) {
            case -1: return "Up";
            case -2: return "Down";
            case -3: return "Left";
            case -4: return "Right";
            case -5: return "Select";
            case -6: return "Soft1";
            case -7: return "Soft2";
            default: return "Key" + keyCode;
        }
    }

    public void b22Show() { showNotify(); }
    public void b22Hide() { hideNotify(); }

    public void b22Paint(Graphics g) {
        Graphics own = new Graphics(null, Display.WIDTH, getHeight());
        own.setClip(g.getClipX(), g.getClipY(), g.getClipWidth(), g.getClipHeight());
        paint(own);
        paintCommands(g);
    }

    public void b22Key(int type, int code) {
        if (softKey(type, code)) return;
        if (type == Events.KEY_PRESSED) keyPressed(code);
        else if (type == Events.KEY_RELEASED) keyReleased(code);
        else if (type == Events.KEY_REPEATED) keyRepeated(code);
    }
}
