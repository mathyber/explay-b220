package java.lang;

public final class Class {
    /* Native Class pointer, set by the VM. */
    int vmClass;

    private Class() {}

    public static native Class forName(String name) throws ClassNotFoundException;
    public native Object newInstance() throws InstantiationException, IllegalAccessException;
    public native String getName();
    public native boolean isInstance(Object o);
    public native boolean isArray();
    public native boolean isInterface();

    public String toString() {
        return (isInterface() ? "interface " : "class ") + getName();
    }
}
