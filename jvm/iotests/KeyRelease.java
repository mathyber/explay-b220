import javax.microedition.lcdui.Canvas;
import javax.microedition.lcdui.Display;
import javax.microedition.lcdui.Graphics;
import javax.microedition.midlet.MIDlet;

/* The first keyReleased ends the MIDlet with its key code on the console (an
   Error: Display catches RuntimeExceptions from callbacks). The
   release of OK that launched it must not arrive: its press came before. */
public class KeyRelease extends MIDlet {
    protected void startApp() {
        Display.getDisplay(this).setCurrent(new Canvas() {
            protected void paint(Graphics g) { g.fillRect(0, 0, 10, 10); }
            protected void keyReleased(int code) { throw new Error("released " + code); }
        });
    }

    protected void pauseApp() {}

    protected void destroyApp(boolean unconditional) {}
}
