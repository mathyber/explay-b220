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

    private static native byte[] readResource(String path);
    /* Stream state for a large resource, or null: read it whole instead. */
    private static native byte[] openResource(String path);

    public java.io.InputStream getResourceAsStream(String name) {
        if (name.startsWith("/")) name = name.substring(1);
        else {
            String className = getName();
            int dot = className.lastIndexOf('.');
            if (dot >= 0) name = className.substring(0, dot + 1).replace('.', '/') + name;
        }
        byte[] state = openResource(name);
        if (state != null) return new go1.ResourceStream(state);
        byte[] data = readResource(name);
        return data == null ? null : new java.io.ByteArrayInputStream(data);
    }

    public String toString() {
        return (isInterface() ? "interface " : "class ") + getName();
    }
}
