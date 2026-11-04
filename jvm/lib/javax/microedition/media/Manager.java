package javax.microedition.media;

import java.io.InputStream;

/* No sound output yet: players go through their states silently. */
public final class Manager {
    public static final String TONE_DEVICE_LOCATOR = "device://tone";

    private Manager() {}

    public static String[] getSupportedContentTypes(String protocol) {
        return new String[] {"audio/midi", "audio/x-wav", "audio/x-tone-seq"};
    }

    public static String[] getSupportedProtocols(String contentType) {
        return new String[0];
    }

    public static Player createPlayer(InputStream stream, String type) throws java.io.IOException, MediaException {
        if (stream == null) throw new IllegalArgumentException();
        return new go1.SilentPlayer(type);
    }

    public static Player createPlayer(String locator) throws java.io.IOException, MediaException {
        if (locator == null) throw new IllegalArgumentException();
        return new go1.SilentPlayer(null);
    }

    public static void playTone(int note, int duration, int volume) throws MediaException {
        if (note < 0 || note > 127 || duration <= 0) throw new IllegalArgumentException();
    }
}
