import javax.microedition.lcdui.Command;
import javax.microedition.lcdui.CommandListener;
import javax.microedition.lcdui.Display;
import javax.microedition.lcdui.Displayable;
import javax.microedition.lcdui.Font;
import javax.microedition.lcdui.Graphics;
import javax.microedition.lcdui.game.GameCanvas;
import javax.microedition.midlet.MIDlet;

/* MIDP demo for the B220: a GameCanvas loop with a ball, a paddle and an FPS counter. */
public class Bounce extends MIDlet implements CommandListener {
    private final Command exit = new Command("Выход", Command.EXIT, 1);
    private Field field;

    class Field extends GameCanvas implements Runnable {
        volatile boolean running = true;
        int x = 40, y = 60, dx = 3, dy = 2, paddle = 70, hits, frames, fps;
        long second = System.currentTimeMillis();

        Field() {
            super(true);
            setFullScreenMode(false);
            addCommand(exit);
            setCommandListener(Bounce.this);
        }

        public void run() {
            Graphics g = getGraphics();
            Font title = Font.getFont(Font.FACE_SYSTEM, Font.STYLE_BOLD, Font.SIZE_MEDIUM);
            Font small = Font.getFont(Font.FACE_SYSTEM, Font.STYLE_PLAIN, Font.SIZE_SMALL);
            int height = getHeight();
            while (running) {
                int keys = getKeyStates();
                if ((keys & LEFT_PRESSED) != 0 && paddle > 0) paddle -= 6;
                if ((keys & RIGHT_PRESSED) != 0 && paddle < 176 - 40) paddle += 6;
                x += dx;
                y += dy;
                if (x < 4 || x > 166) dx = -dx;
                if (y < 24) dy = -dy;
                if (y > height - 16 && x >= paddle - 4 && x <= paddle + 44 && dy > 0) { dy = -dy; hits++; }
                if (y > height) { y = 40; hits = 0; }
                g.setColor(0x10183c);
                g.fillRect(0, 0, 176, height);
                g.setColor(0xffffff);
                g.setFont(title);
                g.drawString("Мячик на B220", 88, 2, Graphics.TOP | Graphics.HCENTER);
                g.setFont(small);
                g.drawString("отбито: " + hits + "   кадров/с: " + fps, 88, title.getHeight() + 2, Graphics.TOP | Graphics.HCENTER);
                g.setColor(0xffcc00);
                g.fillArc(x - 5, y - 5, 10, 10, 0, 360);
                g.setColor(0x40c0ff);
                g.fillRoundRect(paddle, height - 10, 40, 6, 6, 6);
                flushGraphics();
                frames++;
                long now = System.currentTimeMillis();
                if (now - second >= 1000) { fps = frames; frames = 0; second = now; }
                try {
                    Thread.sleep(30);
                } catch (InterruptedException e) {
                    return;
                }
            }
        }
    }

    protected void startApp() {
        field = new Field();
        Display.getDisplay(this).setCurrent(field);
        new Thread(field).start();
    }

    protected void pauseApp() {}

    protected void destroyApp(boolean unconditional) {
        if (field != null) field.running = false;
    }

    public void commandAction(Command c, Displayable d) {
        if (c == exit) {
            destroyApp(true);
            notifyDestroyed();
        }
    }
}
