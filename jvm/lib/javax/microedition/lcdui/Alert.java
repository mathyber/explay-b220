package javax.microedition.lcdui;

import java.util.Vector;

/* Modal message; dismissed by timeout, any key or its command. */
public class Alert extends Screen {
    public static final int FOREVER = -2;
    public static final Command DISMISS_COMMAND = new Command("OK", Command.OK, 0);

    private String text;
    private Image image;
    private AlertType type;
    private int timeout = 2000;
    Displayable next;

    public Alert(String title) {
        this(title, null, null, null);
    }

    public Alert(String title, String text, Image image, AlertType type) {
        this.title = title;
        this.text = text;
        this.image = image;
        this.type = type;
    }

    public int getDefaultTimeout() { return 2000; }
    public int getTimeout() { return timeout; }
    public void setTimeout(int t) { timeout = t; }
    public AlertType getType() { return type; }
    public void setType(AlertType t) { type = t; }
    public String getString() { return text; }
    public void setString(String s) { text = s; b22Changed(); }
    public Image getImage() { return image; }
    public void setImage(Image i) { image = i; b22Changed(); }

    private void dismiss() {
        if (!isShown()) return;
        if (listener != null && commands.size() > 0) listener.commandAction((Command) commands.elementAt(0), this);
        else if (next != null) display.setCurrent(next);
    }

    public void b22Show() {
        if (timeout == FOREVER) return;
        final int wait = timeout;
        new Thread() {
            public void run() {
                try {
                    Thread.sleep(wait);
                } catch (InterruptedException e) {
                    return;
                }
                display.callSerially(new Runnable() {
                    public void run() { if (next != null && isShown()) display.setCurrent(next); }
                });
            }
        }.start();
    }

    public void b22Key(int type, int code) {
        if (type != b22.Events.KEY_PRESSED) return;
        if (!softKey(type, code)) dismiss();
    }

    public void b22Paint(Graphics g) {
        int y = paintTitle(g) + 4;
        if (image != null) {
            g.drawImage(image, Display.WIDTH / 2, y, Graphics.TOP | Graphics.HCENTER);
            y += image.getHeight() + 4;
        }
        if (text != null) {
            Font f = Font.getDefaultFont();
            g.setFont(f);
            g.setColor(0x000000);
            Vector lines = new Vector();
            wrap(text, f, Display.WIDTH - 8, lines);
            for (int i = 0; i < lines.size(); i++, y += f.getHeight())
                g.drawString((String) lines.elementAt(i), 4, y, Graphics.TOP | Graphics.LEFT);
        }
        paintCommands(g);
    }
}
