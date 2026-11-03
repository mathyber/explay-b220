package javax.microedition.midlet;

public abstract class MIDlet {
    protected MIDlet() {}

    protected abstract void startApp() throws MIDletStateChangeException;

    protected abstract void pauseApp();

    protected abstract void destroyApp(boolean unconditional) throws MIDletStateChangeException;

    public final void notifyDestroyed() {
        b22.Launcher.exit();
    }

    public final void notifyPaused() {}

    public final void resumeRequest() {}

    public final String getAppProperty(String key) {
        return b22.Launcher.getProperty(key);
    }

    public final boolean platformRequest(String url) {
        return false;
    }

    public final int checkPermission(String permission) {
        return 1;
    }

    /* Called by b22.Launcher. */
    public final void b22Start() throws MIDletStateChangeException {
        startApp();
    }

    public final void b22Destroy() {
        try {
            destroyApp(true);
        } catch (MIDletStateChangeException e) {
            // unconditional destruction ignores the request
        }
    }
}
