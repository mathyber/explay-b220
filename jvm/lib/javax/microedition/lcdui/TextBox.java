package javax.microedition.lcdui;

import java.util.Vector;
import go1.Events;

/* Text entry with the phone keypad: multi-tap letters (Cyrillic and Latin),
   '#' switches the mode, '*' deletes before the cursor, holding a digit enters
   it, '0' is a space, '1' punctuation; left/right move the cursor, up/down
   jump to the start/end. Commands sit on the soft keys. */
public class TextBox extends Screen {
    /* TextField constraints (the values are part of the MIDP API). */
    static final int ANY = 0, EMAILADDR = 1, NUMERIC = 2, PHONENUMBER = 3, URL = 4, DECIMAL = 5,
        CONSTRAINT_MASK = 0xffff, PASSWORD = 0x10000;
    private static final String[] MODES = {"абв", "АБВ", "abc", "ABC", "123"};
    private static final String[] CYRILLIC = {" 0", ".,?!-:;'\"()1", "абвг2", "деёжз3", "ийкл4", "мноп5", "рсту6",
        "фхцч7", "шщъы8", "ьэюя9"};
    private static final String[] LATIN = {" 0", ".,?!-:/@_'&=1", "abc2", "def3", "ghi4", "jkl5", "mno6", "pqrs7",
        "tuv8", "wxyz9"};
    private static final int TAP_MS = 1000;

    private StringBuffer text = new StringBuffer();
    private int maxSize, constraints, caret, mode;
    private int lastKey = -1, tap;
    private long lastTime;
    private boolean pending;

    public TextBox(String title, String text, int maxSize, int constraints) {
        if (maxSize <= 0) throw new IllegalArgumentException();
        this.title = title;
        this.maxSize = maxSize;
        setConstraints(constraints);
        setString(text);
    }

    public String getString() { return text.toString(); }

    public void setString(String s) {
        if (s != null && s.length() > maxSize) throw new IllegalArgumentException();
        text = new StringBuffer(s == null ? "" : s);
        caret = text.length();
        pending = false;
        go1Changed();
    }

    public int getChars(char[] data) {
        String s = getString();
        s.getChars(0, s.length(), data, 0);
        return s.length();
    }

    public void setChars(char[] data, int offset, int length) {
        setString(data == null ? "" : new String(data, offset, length));
    }

    public void insert(String s, int position) {
        if (text.length() + s.length() > maxSize) throw new IllegalArgumentException();
        position = Math.max(0, Math.min(position, text.length()));
        text.insert(position, s);
        caret = position + s.length();
        go1Changed();
    }

    public void insert(char[] data, int offset, int length, int position) {
        insert(new String(data, offset, length), position);
    }

    public void delete(int offset, int length) {
        if (offset < 0 || length < 0 || offset + length > text.length()) throw new StringIndexOutOfBoundsException();
        String s = getString();
        text = new StringBuffer(s.substring(0, offset) + s.substring(offset + length));
        caret = Math.min(caret, text.length());
        go1Changed();
    }

    public int getMaxSize() { return maxSize; }

    public int setMaxSize(int size) {
        if (size <= 0) throw new IllegalArgumentException();
        maxSize = size;
        if (text.length() > size) text.setLength(size);
        caret = Math.min(caret, text.length());
        return size;
    }

    public int size() { return text.length(); }
    public int getCaretPosition() { return caret; }
    public int getConstraints() { return constraints; }

    public void setConstraints(int c) {
        constraints = c;
        int kind = c & CONSTRAINT_MASK;
        mode = kind == NUMERIC || kind == PHONENUMBER || kind == DECIMAL ? 4 : kind == URL || kind == EMAILADDR ? 2 : 0;
    }

    public void setInitialInputMode(String characterSubset) {
        if (characterSubset != null && characterSubset.startsWith("UCB_BASIC_LATIN")) mode = 2;
    }

    private boolean numeric() {
        int kind = constraints & CONSTRAINT_MASK;
        return kind == NUMERIC || kind == PHONENUMBER || kind == DECIMAL;
    }

    private void type(char c, boolean replace) {
        if (replace && caret > 0) text.setCharAt(caret - 1, c);
        else if (text.length() < maxSize) text.insert(caret++, c);
    }

    public void go1Key(int kind, int code) {
        if (softKey(kind, code) || kind == Events.KEY_RELEASED) return;
        long now = System.currentTimeMillis();
        int digit = code >= '0' && code <= '9' ? code - '0' : -1;
        if (digit >= 0 && kind == Events.KEY_REPEATED) {
            // Holding a digit: the digit itself instead of the letter.
            if (pending && lastKey == code) type((char) code, true);
            pending = false;
            lastKey = -1;
        } else if (digit >= 0) {
            if (mode == 4) {
                type((char) code, false);
                pending = false;
            } else {
                String keys = (mode < 2 ? CYRILLIC : LATIN)[digit];
                boolean again = pending && lastKey == code && now - lastTime < TAP_MS;
                tap = again ? (tap + 1) % keys.length() : 0;
                char c = keys.charAt(tap);
                if (mode == 1 || mode == 3) c = Character.toUpperCase(c);
                type(c, again);
                pending = true;
            }
            lastKey = code;
            lastTime = now;
        } else {
            pending = false;
            lastKey = -1;
            if (code == '*') {
                if (numeric() && (constraints & CONSTRAINT_MASK) != NUMERIC && kind == Events.KEY_PRESSED)
                    type((constraints & CONSTRAINT_MASK) == DECIMAL ? '.' : '+', false);
                else if (caret > 0) {
                    text.deleteCharAt(--caret);
                }
            } else if (code == '#' && kind == Events.KEY_PRESSED) {
                if (!numeric()) mode = (mode + 1) % MODES.length;
            } else if (code == -3) caret = Math.max(0, caret - 1);
            else if (code == -4) caret = Math.min(text.length(), caret + 1);
            else if (code == -1) caret = 0;
            else if (code == -2) caret = text.length();
            else return;
        }
        go1Changed();
    }

    public void go1Paint(Graphics g) {
        int y = paintTitle(g);
        Font small = Font.getFont(Font.FACE_SYSTEM, Font.STYLE_PLAIN, Font.SIZE_SMALL);
        g.setFont(small);
        g.setColor(0x666666);
        g.drawString(MODES[mode] + "  " + text.length() + "/" + maxSize, Display.WIDTH - 4, y + 2, Graphics.TOP | Graphics.RIGHT);
        y += small.getHeight() + 4;
        Font f = Font.getDefaultFont();
        g.setFont(f);
        String shown = getString();
        if ((constraints & PASSWORD) != 0) {
            StringBuffer stars = new StringBuffer();
            for (int i = 0; i < shown.length(); i++) stars.append(i == caret - 1 && pending ? shown.charAt(i) : '*');
            shown = stars.toString();
        }
        int width = Display.WIDTH - 8, bottom = getHeight() - 2;
        g.setColor(0xaaaaaa);
        g.drawRect(2, y - 2, Display.WIDTH - 5, bottom - y + 2);
        Vector lines = new Vector();
        // Wrap by characters so the caret maps to a line and column.
        int start = 0;
        while (start < shown.length()) {
            int end = start;
            while (end < shown.length() && shown.charAt(end) != '\n' && f.substringWidth(shown, start, end - start + 1) <= width) end++;
            if (end == start) end++;
            lines.addElement(new int[] {start, end});
            start = end < shown.length() && shown.charAt(end) == '\n' ? end + 1 : end;
        }
        if (lines.isEmpty() || ((int[]) lines.lastElement())[1] < caret) lines.addElement(new int[] {start, start});
        int caretLine = 0;
        for (int i = 0; i < lines.size(); i++) {
            int[] l = (int[]) lines.elementAt(i);
            if (caret >= l[0] && caret <= l[1]) caretLine = i;
        }
        int visible = Math.max(1, (bottom - y) / f.getHeight());
        int first = Math.max(0, caretLine - visible + 1);
        for (int i = first; i < lines.size() && i < first + visible; i++) {
            int[] l = (int[]) lines.elementAt(i);
            int ly = y + (i - first) * f.getHeight();
            g.setColor(0x000000);
            g.drawSubstring(shown, l[0], l[1] - l[0], 4, ly, Graphics.TOP | Graphics.LEFT);
            if (i == caretLine) {
                int cx = 4 + f.substringWidth(shown, l[0], caret - l[0]);
                if (pending && caret > l[0]) {
                    int cw = f.charWidth(shown.charAt(caret - 1));
                    g.setColor(0x3366cc);
                    g.drawLine(cx - cw, ly + f.getHeight() - 1, cx - 1, ly + f.getHeight() - 1);
                } else {
                    g.setColor(0xcc0000);
                    g.drawLine(cx, ly, cx, ly + f.getHeight() - 2);
                }
            }
        }
        g.setClip(0, 0, Display.WIDTH, Display.HEIGHT);
        paintCommands(g);
    }
}
