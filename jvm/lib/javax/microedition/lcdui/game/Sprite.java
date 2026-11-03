package javax.microedition.lcdui.game;

import javax.microedition.lcdui.Graphics;
import javax.microedition.lcdui.Image;

/* Animated sprite with frames, a frame sequence, a reference pixel and the
   eight transforms. Collisions use rectangles only (pixel-level is false). */
public class Sprite extends Layer {
    public static final int TRANS_NONE = 0;
    public static final int TRANS_ROT90 = 5;
    public static final int TRANS_ROT180 = 3;
    public static final int TRANS_ROT270 = 6;
    public static final int TRANS_MIRROR = 2;
    public static final int TRANS_MIRROR_ROT90 = 7;
    public static final int TRANS_MIRROR_ROT180 = 1;
    public static final int TRANS_MIRROR_ROT270 = 4;

    private Image image;
    private int frameWidth, frameHeight, columns, rawFrames;
    private int[] sequence;
    private int frame, transform;
    private int refX, refY;
    private int collisionX, collisionY, collisionWidth, collisionHeight;

    public Sprite(Image image) {
        this(image, image.getWidth(), image.getHeight());
    }

    public Sprite(Image image, int frameWidth, int frameHeight) {
        super(frameWidth, frameHeight);
        setImage(image, frameWidth, frameHeight);
    }

    public Sprite(Sprite s) {
        super(s.width, s.height);
        setImage(s.image, s.frameWidth, s.frameHeight);
        sequence = (int[]) s.sequence.clone();
        frame = s.frame;
        transform = s.transform;
        refX = s.refX;
        refY = s.refY;
        x = s.x;
        y = s.y;
        visible = s.visible;
        defineCollisionRectangle(s.collisionX, s.collisionY, s.collisionWidth, s.collisionHeight);
    }

    public void setImage(Image image, int frameWidth, int frameHeight) {
        if (image == null) throw new NullPointerException();
        if (frameWidth < 1 || frameHeight < 1 || image.getWidth() % frameWidth != 0 || image.getHeight() % frameHeight != 0)
            throw new IllegalArgumentException();
        int previous = rawFrames;
        this.image = image;
        this.frameWidth = frameWidth;
        this.frameHeight = frameHeight;
        columns = image.getWidth() / frameWidth;
        rawFrames = columns * (image.getHeight() / frameHeight);
        if (sequence == null || rawFrames < previous) {
            sequence = new int[rawFrames];
            for (int i = 0; i < rawFrames; i++) sequence[i] = i;
            frame = 0;
        }
        defineCollisionRectangle(0, 0, frameWidth, frameHeight);
        setTransform(transform);
    }

    public void defineReferencePixel(int x, int y) {
        refX = x;
        refY = y;
    }

    public void setRefPixelPosition(int x, int y) {
        int[] p = transformed(refX, refY);
        this.x = x - p[0];
        this.y = y - p[1];
    }

    public int getRefPixelX() { return x + transformed(refX, refY)[0]; }
    public int getRefPixelY() { return y + transformed(refX, refY)[1]; }

    public void setFrame(int index) {
        if (index < 0 || index >= sequence.length) throw new IndexOutOfBoundsException();
        frame = index;
    }

    public final int getFrame() { return frame; }
    public int getRawFrameCount() { return rawFrames; }
    public int getFrameSequenceLength() { return sequence.length; }
    public void nextFrame() { frame = (frame + 1) % sequence.length; }
    public void prevFrame() { frame = (frame + sequence.length - 1) % sequence.length; }

    public void setFrameSequence(int[] s) {
        if (s == null) {
            sequence = new int[rawFrames];
            for (int i = 0; i < rawFrames; i++) sequence[i] = i;
        } else {
            if (s.length == 0) throw new IllegalArgumentException();
            for (int i = 0; i < s.length; i++) if (s[i] < 0 || s[i] >= rawFrames) throw new ArrayIndexOutOfBoundsException();
            sequence = (int[]) s.clone();
        }
        frame = 0;
    }

    public void setTransform(int t) {
        if (t < 0 || t > 7) throw new IllegalArgumentException();
        // Keep the reference pixel where it is on the screen.
        int[] before = transformed(refX, refY);
        transform = t;
        int[] after = transformed(refX, refY);
        x += before[0] - after[0];
        y += before[1] - after[1];
        boolean turned = t >= 4;
        width = turned ? frameHeight : frameWidth;
        height = turned ? frameWidth : frameHeight;
    }

    /* Position of a frame pixel inside the transformed frame. */
    private int[] transformed(int px, int py) {
        int w = frameWidth, h = frameHeight;
        switch (transform) {
            case TRANS_MIRROR_ROT180: return new int[] {px, h - 1 - py};
            case TRANS_MIRROR: return new int[] {w - 1 - px, py};
            case TRANS_ROT180: return new int[] {w - 1 - px, h - 1 - py};
            case TRANS_MIRROR_ROT270: return new int[] {py, px};
            case TRANS_ROT90: return new int[] {h - 1 - py, px};
            case TRANS_ROT270: return new int[] {py, w - 1 - px};
            case TRANS_MIRROR_ROT90: return new int[] {h - 1 - py, w - 1 - px};
            default: return new int[] {px, py};
        }
    }

    public void defineCollisionRectangle(int x, int y, int w, int h) {
        if (w < 0 || h < 0) throw new IllegalArgumentException();
        collisionX = x;
        collisionY = y;
        collisionWidth = w;
        collisionHeight = h;
    }

    private int[] collisionBox() {
        int[] a = transformed(collisionX, collisionY);
        int[] b = transformed(collisionX + collisionWidth - 1, collisionY + collisionHeight - 1);
        int left = Math.min(a[0], b[0]), top = Math.min(a[1], b[1]);
        return new int[] {x + left, y + top, Math.abs(a[0] - b[0]) + 1, Math.abs(a[1] - b[1]) + 1};
    }

    private static boolean overlap(int[] a, int x, int y, int w, int h) {
        return a[2] > 0 && a[3] > 0 && w > 0 && h > 0 && a[0] < x + w && x < a[0] + a[2] && a[1] < y + h && y < a[1] + a[3];
    }

    public final boolean collidesWith(Sprite s, boolean pixelLevel) {
        if (!visible || !s.visible) return false;
        int[] b = s.collisionBox();
        return overlap(collisionBox(), b[0], b[1], b[2], b[3]);
    }

    public final boolean collidesWith(Image image, int x, int y, boolean pixelLevel) {
        return visible && overlap(collisionBox(), x, y, image.getWidth(), image.getHeight());
    }

    public final void paint(Graphics g) {
        if (!visible) return;
        int f = sequence[frame];
        g.drawRegion(image, f % columns * frameWidth, f / columns * frameHeight, frameWidth, frameHeight,
            transform, x, y, Graphics.TOP | Graphics.LEFT);
    }
}
