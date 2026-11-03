package javax.microedition.media;

public interface PlayerListener {
    String STARTED = "started";
    String STOPPED = "stopped";
    String END_OF_MEDIA = "endOfMedia";
    String CLOSED = "closed";
    String VOLUME_CHANGED = "volumeChanged";

    void playerUpdate(Player player, String event, Object data);
}
