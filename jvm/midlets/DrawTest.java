import javax.microedition.lcdui.Canvas;
import javax.microedition.lcdui.Command;
import javax.microedition.lcdui.CommandListener;
import javax.microedition.lcdui.Display;
import javax.microedition.lcdui.Displayable;
import javax.microedition.lcdui.Font;
import javax.microedition.lcdui.Graphics;
import javax.microedition.lcdui.Image;
import javax.microedition.midlet.MIDlet;

/* MIDP drawing test: tests/test_jvm.py checks pixels of the host screenshot. */
public class DrawTest extends MIDlet implements CommandListener {
    private final Command exit = new Command("Exit", Command.EXIT, 1);

    class Board extends Canvas {
        int background = 0x000080;
        Image sprite, arrow;
        int presses;

        Board() {
            setFullScreenMode(false);
            addCommand(exit);
            setCommandListener(DrawTest.this);
            try {
                sprite = Image.createImage("/sprite.png");
            } catch (Exception e) {
                System.out.println("sprite failed " + e);
            }
            arrow = Image.createImage(8, 4);
            Graphics g = arrow.getGraphics();
            g.setColor(0xff0000);
            g.fillRect(0, 0, 8, 4);
            g.setColor(0x00ff00);
            g.fillRect(0, 0, 2, 4);
        }

        protected void paint(Graphics g) {
            g.setColor(background);
            g.fillRect(0, 0, getWidth(), getHeight());
            g.setColor(0xff0000);
            g.fillRect(10, 10, 40, 30);
            g.setColor(0xffffff);
            g.drawLine(0, 60, 175, 60);
            g.setColor(0xffff00);
            g.fillTriangle(100, 10, 140, 10, 120, 40);
            g.setColor(0xffffff);
            g.setFont(Font.getFont(Font.FACE_SYSTEM, Font.STYLE_PLAIN, Font.SIZE_LARGE));
            g.drawString("Привет, B220!", 88, 70, Graphics.TOP | Graphics.HCENTER);
            if (sprite != null) g.drawImage(sprite, 10, 100, Graphics.TOP | Graphics.LEFT);
            g.drawRegion(arrow, 0, 0, 8, 4, 5, 60, 100, Graphics.TOP | Graphics.LEFT);
            int[] half = new int[16 * 16];
            for (int i = 0; i < half.length; i++) half[i] = 0x80ffffff;
            g.drawRGB(half, 0, 16, 100, 100, 16, 16, true);
            g.translate(130, 100);
            g.setClip(0, 0, 10, 10);
            g.setColor(0x00ffff);
            g.fillRect(-5, -5, 40, 40);
        }

        protected void keyPressed(int code) {
            if (getGameAction(code) == FIRE) {
                presses++;
                background = 0x008000;
                repaint();
            }
        }
    }

    protected void startApp() {
        System.out.println("startApp " + getAppProperty("MIDlet-Name"));
        Display.getDisplay(this).setCurrent(new Board());
    }

    protected void pauseApp() {}

    protected void destroyApp(boolean unconditional) {
        System.out.println("destroyApp");
    }

    public void commandAction(Command c, Displayable d) {
        if (c == exit) notifyDestroyed();
    }
}
