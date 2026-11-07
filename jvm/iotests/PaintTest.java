import javax.microedition.lcdui.Canvas;
import javax.microedition.lcdui.Graphics;
import javax.microedition.lcdui.Image;

/* Every paint() gets the same Graphics, its state reset (phones do this, and
   games keep objects made from it, such as Nokia's DirectGraphics). */
public class PaintTest extends Canvas {
    Graphics first;
    int paints;
    String report = "";

    protected void paint(Graphics g) {
        paints++;
        if (first == null) first = g;
        report += (g == first) + " " + g.getTranslateX() + "," + g.getTranslateY() + " " + g.getColor() + " "
                + g.getClipX() + "," + g.getClipY() + "," + g.getClipWidth() + "," + g.getClipHeight() + "; ";
        /* leave state behind for the next paint */
        g.translate(5, 7);
        g.setColor(0x123456);
        g.setClip(1, 2, 3, 4);
    }

    public static void main(String[] args) {
        PaintTest c = new PaintTest();
        Graphics target = Image.createImage(176, 208).getGraphics();
        target.setClip(10, 20, 30, 40);
        c.go1Paint(target);
        target.setClip(0, 0, 176, 100);
        c.go1Paint(target);
        System.out.println(c.report);
    }
}
