package java.lang;

public class Throwable {
    /* Read by the VM for uncaught exception reports. */
    private String detailMessage;

    public Throwable() {}

    public Throwable(String message) {
        detailMessage = message;
    }

    public String getMessage() {
        return detailMessage;
    }

    public String toString() {
        String name = getClass().getName();
        return detailMessage == null ? name : name + ": " + detailMessage;
    }

    public void printStackTrace() {
        System.err.println(this);
    }
}
