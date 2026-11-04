package javax.microedition.lcdui;

import go1.Draw;
import java.io.InputStream;

/* Bitmap fonts with Latin-1 and Cyrillic in three sizes (go1/font-*.bin). */
public final class Font {
    public static final int FACE_SYSTEM = 0;
    public static final int FACE_MONOSPACE = 32;
    public static final int FACE_PROPORTIONAL = 64;
    public static final int STYLE_PLAIN = 0;
    public static final int STYLE_BOLD = 1;
    public static final int STYLE_ITALIC = 2;
    public static final int STYLE_UNDERLINED = 4;
    public static final int SIZE_SMALL = 8;
    public static final int SIZE_MEDIUM = 0;
    public static final int SIZE_LARGE = 16;
    public static final int FONT_STATIC_TEXT = 0;
    public static final int FONT_INPUT_TEXT = 1;

    private static final byte[][] DATA = new byte[3][];
    private static Font defaultFont;

    final byte[] data;
    private final int face, style, size;

    private Font(int face, int style, int size) {
        this.face = face;
        this.style = style;
        this.size = size;
        data = load(size == SIZE_SMALL ? 0 : size == SIZE_LARGE ? 2 : 1);
    }

    private static byte[] load(int index) {
        if (DATA[index] == null) {
            try {
                InputStream in = Font.class.getResourceAsStream("/go1/font-" + "sml".charAt(index) + ".bin");
                byte[] b = new byte[in.available()];
                int n = 0;
                while (n < b.length) {
                    int r = in.read(b, n, b.length - n);
                    if (r < 0) break;
                    n += r;
                }
                DATA[index] = b;
            } catch (Exception e) {
                throw new RuntimeException("font missing");
            }
        }
        return DATA[index];
    }

    public static Font getDefaultFont() {
        if (defaultFont == null) defaultFont = new Font(FACE_SYSTEM, STYLE_PLAIN, SIZE_MEDIUM);
        return defaultFont;
    }

    public static Font getFont(int face, int style, int size) {
        if ((face != FACE_SYSTEM && face != FACE_MONOSPACE && face != FACE_PROPORTIONAL)
            || (size != SIZE_SMALL && size != SIZE_MEDIUM && size != SIZE_LARGE) || (style & ~7) != 0)
            throw new IllegalArgumentException();
        return new Font(face, style, size);
    }

    public static Font getFont(int specifier) {
        if (specifier != FONT_STATIC_TEXT && specifier != FONT_INPUT_TEXT) throw new IllegalArgumentException();
        return getDefaultFont();
    }

    public int getFace() { return face; }
    public int getStyle() { return style; }
    public int getSize() { return size; }
    public boolean isPlain() { return style == STYLE_PLAIN; }
    public boolean isBold() { return (style & STYLE_BOLD) != 0; }
    public boolean isItalic() { return (style & STYLE_ITALIC) != 0; }
    public boolean isUnderlined() { return (style & STYLE_UNDERLINED) != 0; }
    public int getHeight() { return data[3] & 0xff; }
    public int getBaselinePosition() { return data[4] & 0xff; }

    public int charWidth(char c) {
        return substringWidth(String.valueOf(c), 0, 1);
    }

    public int charsWidth(char[] c, int offset, int length) {
        return substringWidth(new String(c, offset, length), 0, length);
    }

    public int stringWidth(String s) {
        return substringWidth(s, 0, s.length());
    }

    public int substringWidth(String s, int offset, int length) {
        return Draw.textWidth(s, offset, length, data) + (isBold() ? length : 0);
    }
}
