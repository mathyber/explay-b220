package javax.microedition.lcdui;

import go1.Draw;

/* Translation, clip, colour and font state; pixels are drawn by go1.Draw. */
public class Graphics {
    public static final int HCENTER = 1;
    public static final int VCENTER = 2;
    public static final int LEFT = 4;
    public static final int RIGHT = 8;
    public static final int TOP = 16;
    public static final int BOTTOM = 32;
    public static final int BASELINE = 64;
    public static final int SOLID = 0;
    public static final int DOTTED = 1;

    private final Object target;
    private final int width, height;
    private int tx, ty;
    private int cx, cy, cw, ch;  /* absolute clip */
    private int color;
    private int stroke;
    private Font font;

    Graphics(Object target, int width, int height) {
        this.target = target;
        this.width = width;
        this.height = height;
        cw = width;
        ch = height;
        font = Font.getDefaultFont();
    }

    /* The state paint() starts with: Canvas reuses one Graphics for every paint. */
    void reset(int x, int y, int w, int h) {
        tx = ty = 0;
        color = 0;
        stroke = SOLID;
        font = Font.getDefaultFont();
        setClip(x, y, w, h);
    }

    public void translate(int x, int y) { tx += x; ty += y; }
    public int getTranslateX() { return tx; }
    public int getTranslateY() { return ty; }

    public int getColor() { return color; }
    public int getRedComponent() { return (color >> 16) & 0xff; }
    public int getGreenComponent() { return (color >> 8) & 0xff; }
    public int getBlueComponent() { return color & 0xff; }
    public int getGrayScale() { return (getRedComponent() + getGreenComponent() + getBlueComponent()) / 3; }
    public void setColor(int rgb) { color = rgb & 0xffffff; }

    public void setColor(int r, int g, int b) {
        if ((r | g | b) < 0 || r > 255 || g > 255 || b > 255) throw new IllegalArgumentException();
        color = r << 16 | g << 8 | b;
    }

    public void setGrayScale(int v) { setColor(v, v, v); }
    public int getDisplayColor(int rgb) { return rgb & 0xf8fcf8; }

    public Font getFont() { return font; }
    public void setFont(Font f) { font = f == null ? Font.getDefaultFont() : f; }
    public int getStrokeStyle() { return stroke; }

    public void setStrokeStyle(int style) {
        if (style != SOLID && style != DOTTED) throw new IllegalArgumentException();
        stroke = style;
    }

    public int getClipX() { return cx - tx; }
    public int getClipY() { return cy - ty; }
    public int getClipWidth() { return cw; }
    public int getClipHeight() { return ch; }

    /* Comparisons inline: games set the clip many times per frame. */
    public void setClip(int x, int y, int w, int h) {
        int x0 = x + tx, y0 = y + ty, x1 = x0 + w, y1 = y0 + h;
        if (x0 < 0) x0 = 0;
        if (y0 < 0) y0 = 0;
        if (x1 > width) x1 = width;
        if (y1 > height) y1 = height;
        cx = x0;
        cy = y0;
        cw = x1 > x0 ? x1 - x0 : 0;
        ch = y1 > y0 ? y1 - y0 : 0;
    }

    public void clipRect(int x, int y, int w, int h) {
        int x0 = x + tx, y0 = y + ty, x1 = x0 + w, y1 = y0 + h;
        if (x0 < cx) x0 = cx;
        if (y0 < cy) y0 = cy;
        if (x1 > cx + cw) x1 = cx + cw;
        if (y1 > cy + ch) y1 = cy + ch;
        cx = x0;
        cy = y0;
        cw = x1 > x0 ? x1 - x0 : 0;
        ch = y1 > y0 ? y1 - y0 : 0;
    }

    public void drawLine(int x1, int y1, int x2, int y2) {
        Draw.drawLine(target, x1 + tx, y1 + ty, x2 + tx, y2 + ty, color, cx, cy, cw, ch);
    }

    public void fillRect(int x, int y, int w, int h) {
        if (w > 0 && h > 0) Draw.fillRect(target, x + tx, y + ty, w, h, color, cx, cy, cw, ch);
    }

    public void drawRect(int x, int y, int w, int h) {
        if (w < 0 || h < 0) return;
        fillRect(x, y, w + 1, 1);
        fillRect(x, y + h, w + 1, 1);
        fillRect(x, y, 1, h + 1);
        fillRect(x + w, y, 1, h + 1);
    }

    public void fillRoundRect(int x, int y, int w, int h, int aw, int ah) {
        int r = Math.min(Math.min(aw, ah) / 2, Math.min(w, h) / 2);
        if (r <= 1) { fillRect(x, y, w, h); return; }
        fillRect(x + r, y, w - 2 * r, h);
        fillRect(x, y + r, r, h - 2 * r);
        fillRect(x + w - r, y + r, r, h - 2 * r);
        fillArc(x, y, 2 * r, 2 * r, 90, 90);
        fillArc(x + w - 2 * r, y, 2 * r, 2 * r, 0, 90);
        fillArc(x, y + h - 2 * r, 2 * r, 2 * r, 180, 90);
        fillArc(x + w - 2 * r, y + h - 2 * r, 2 * r, 2 * r, 270, 90);
    }

    public void drawRoundRect(int x, int y, int w, int h, int aw, int ah) {
        int r = Math.min(Math.min(aw, ah) / 2, Math.min(w, h) / 2);
        if (r <= 1) { drawRect(x, y, w, h); return; }
        drawLine(x + r, y, x + w - r, y);
        drawLine(x + r, y + h, x + w - r, y + h);
        drawLine(x, y + r, x, y + h - r);
        drawLine(x + w, y + r, x + w, y + h - r);
        drawArc(x, y, 2 * r, 2 * r, 90, 90);
        drawArc(x + w - 2 * r, y, 2 * r, 2 * r, 0, 90);
        drawArc(x, y + h - 2 * r, 2 * r, 2 * r, 180, 90);
        drawArc(x + w - 2 * r, y + h - 2 * r, 2 * r, 2 * r, 270, 90);
    }

    /* Arcs as polygons: angles in degrees, counter-clockwise from 3 o'clock. */
    private static final int[] SIN = new int[91];
    static {
        for (int i = 0; i <= 90; i++) SIN[i] = (int) (Math.sin(Math.toRadians(i)) * 65536 + 0.5);
    }

    private static int sin(int degrees) {
        degrees %= 360;
        if (degrees < 0) degrees += 360;
        if (degrees <= 90) return SIN[degrees];
        if (degrees <= 180) return SIN[180 - degrees];
        if (degrees <= 270) return -SIN[degrees - 180];
        return -SIN[360 - degrees];
    }

    private static int cos(int degrees) { return sin(degrees + 90); }

    public void fillArc(int x, int y, int w, int h, int start, int arc) {
        if (w <= 0 || h <= 0 || arc == 0) return;
        if (arc < 0) { start += arc; arc = -arc; }
        if (arc > 360) arc = 360;
        int cx2 = 2 * x + w, cy2 = 2 * y + h;
        int step = Math.max(3, Math.min(15, arc / 8));
        int px = 0, py = 0;
        for (int a = 0; a <= arc; a += step) {
            int angle = start + Math.min(a, arc);
            int nx = (cx2 + (int) ((long) w * cos(angle) >> 16)) / 2, ny = (cy2 - (int) ((long) h * sin(angle) >> 16)) / 2;
            if (a > 0) fillTriangle(cx2 / 2, cy2 / 2, px, py, nx, ny);
            px = nx;
            py = ny;
            if (a < arc && a + step > arc) a = arc - step;
        }
    }

    public void drawArc(int x, int y, int w, int h, int start, int arc) {
        if (w < 0 || h < 0 || arc == 0) return;
        if (arc < 0) { start += arc; arc = -arc; }
        if (arc > 360) arc = 360;
        int cx2 = 2 * x + w, cy2 = 2 * y + h;
        int step = Math.max(3, Math.min(15, arc / 8));
        int px = 0, py = 0;
        for (int a = 0; a <= arc; a += step) {
            int angle = start + Math.min(a, arc);
            int nx = (cx2 + (int) ((long) w * cos(angle) >> 16)) / 2, ny = (cy2 - (int) ((long) h * sin(angle) >> 16)) / 2;
            if (a > 0) drawLine(px, py, nx, ny);
            px = nx;
            py = ny;
            if (a < arc && a + step > arc) a = arc - step;
        }
    }

    public void fillTriangle(int x1, int y1, int x2, int y2, int x3, int y3) {
        Draw.fillTriangle(target, x1 + tx, y1 + ty, x2 + tx, y2 + ty, x3 + tx, y3 + ty, color, cx, cy, cw, ch);
    }

    private static int left(int x, int w, int anchor) {
        if ((anchor & HCENTER) != 0) return x - w / 2;
        if ((anchor & RIGHT) != 0) return x - w;
        return x;
    }

    public void drawString(String s, int x, int y, int anchor) {
        drawSubstring(s, 0, s.length(), x, y, anchor);
    }

    public void drawSubstring(String s, int offset, int length, int x, int y, int anchor) {
        if (anchor == 0) anchor = TOP | LEFT;
        int w = font.substringWidth(s, offset, length);
        int top = y;
        if ((anchor & BASELINE) != 0) top = y - font.getBaselinePosition();
        else if ((anchor & BOTTOM) != 0) top = y - font.getHeight();
        int x0 = left(x, w, anchor) + tx;
        Draw.drawText(target, s, offset, length, x0, top + ty, color, font.data, font.isBold(), cx, cy, cw, ch);
        if (font.isUnderlined()) fillRect(x0 - tx, top + font.getBaselinePosition() + 1, w, 1);
    }

    public void drawChar(char c, int x, int y, int anchor) {
        drawString(String.valueOf(c), x, y, anchor);
    }

    public void drawChars(char[] data, int offset, int length, int x, int y, int anchor) {
        drawString(new String(data, offset, length), x, y, anchor);
    }

    public void drawImage(Image img, int x, int y, int anchor) {
        drawRegion(img, 0, 0, img.getWidth(), img.getHeight(), 0, x, y, anchor);
    }

    public void drawRegion(Image src, int sx, int sy, int w, int h, int transform, int x, int y, int anchor) {
        if (src == null) throw new NullPointerException();
        if (transform < 0 || transform > 7) throw new IllegalArgumentException();
        if (anchor == 0) anchor = TOP | LEFT;
        int dw = transform >= 4 ? h : w, dh = transform >= 4 ? w : h;
        int x0 = left(x, dw, anchor), y0 = y;
        if ((anchor & VCENTER) != 0) y0 = y - dh / 2;
        else if ((anchor & BOTTOM) != 0) y0 = y - dh;
        Draw.drawRegion(target, src, sx, sy, w, h, transform, x0 + tx, y0 + ty, cx, cy, cw, ch);
    }

    public void drawRGB(int[] rgb, int offset, int scan, int x, int y, int w, int h, boolean processAlpha) {
        Draw.drawRGB(target, rgb, offset, scan, x + tx, y + ty, w, h, processAlpha, cx, cy, cw, ch);
    }

    public void copyArea(int sx, int sy, int w, int h, int dx, int dy, int anchor) {
        if (target == null) throw new IllegalStateException();
        Image copy = Image.createImage((Image) target, sx + tx, sy + ty, w, h, 0);
        drawImage(copy, dx, dy, anchor);
    }
}
