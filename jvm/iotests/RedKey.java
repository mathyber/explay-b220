import javax.microedition.lcdui.Canvas;
import javax.microedition.lcdui.Display;
import javax.microedition.lcdui.Graphics;
import javax.microedition.midlet.MIDlet;

/* Red key test: a thread that never ends (like Opera Mini's), and destroyApp
   that leaves a file behind. RedKeyStuck's destroyApp never returns. */
public class RedKey extends MIDlet {
    protected void startApp() {
        Display.getDisplay(this).setCurrent(new Canvas() {
            protected void paint(Graphics g) { g.fillRect(0, 0, 10, 10); }
        });
        new Thread() {
            public void run() {
                while (true) {
                    try { Thread.sleep(10); } catch (InterruptedException e) {}
                }
            }
        }.start();
    }

    protected void pauseApp() {}

    protected void destroyApp(boolean unconditional) {
        byte[] bye = "bye".getBytes();
        go1.Files.writeAll("E:\\DESTROY.TXT", bye, bye.length);
        if (getClass().getName().equals("RedKeyStuck")) while (true) {}
    }
}
