package java.lang;

public class Runtime {
    private static final Runtime runtime = new Runtime();

    private Runtime() {}

    public static Runtime getRuntime() {
        return runtime;
    }

    public void exit(int status) {
        throw new Error("exit " + status);
    }

    public native long freeMemory();
    public native long totalMemory();
    public native void gc();
}
