package go1;

/* Platform event queue: keys from the phone, repaint wake-ups from Java. */
public final class Events {
    public static final int KEY_PRESSED = 1;
    public static final int KEY_RELEASED = 2;
    public static final int KEY_REPEATED = 3;
    public static final int REPAINT = 4;

    private Events() {}

    /* Fills type and value and returns true, or sleeps briefly and returns false. */
    public static native boolean poll(int[] event);

    public static native void post(int type, int value);
}
