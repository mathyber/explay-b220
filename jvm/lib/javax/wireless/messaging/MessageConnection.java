package javax.wireless.messaging;

import java.io.IOException;
import javax.microedition.io.Connection;

/* Wireless Messaging API (interface only; Connector does not open sms URLs). */
public interface MessageConnection extends Connection {
    String TEXT_MESSAGE = "text";
    String BINARY_MESSAGE = "binary";

    Message newMessage(String type);
    Message newMessage(String type, String address);
    int numberOfSegments(Message message);
    void send(Message message) throws IOException;
    Message receive() throws IOException;
    void setMessageListener(MessageListener listener) throws IOException;
}
