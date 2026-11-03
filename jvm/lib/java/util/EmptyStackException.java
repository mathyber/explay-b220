package java.util;

public class EmptyStackException extends RuntimeException {
    public EmptyStackException() {}

    public EmptyStackException(String message) {
        super(message);
    }
}
