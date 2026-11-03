package javax.microedition.lcdui;

/* High-level screens: a title bar and simple text layout. */
public abstract class Screen extends Displayable {
    Screen() {}

    static int wrap(String text, Font font, int width, java.util.Vector lines) {
        int start = 0;
        while (start < text.length()) {
            int end = start, lastSpace = -1;
            while (end < text.length() && text.charAt(end) != '\n'
                && font.substringWidth(text, start, end - start + 1) <= width) {
                if (text.charAt(end) == ' ') lastSpace = end;
                end++;
            }
            if (end < text.length() && text.charAt(end) != '\n' && lastSpace > start) end = lastSpace;
            if (end == start && end < text.length() && text.charAt(end) != '\n') end++;
            lines.addElement(text.substring(start, end));
            start = end;
            if (start < text.length() && (text.charAt(start) == '\n' || text.charAt(start) == ' ')) start++;
        }
        return lines.size();
    }

    int paintTitle(Graphics g) {
        g.setColor(0xffffff);
        g.fillRect(0, 0, Display.WIDTH, getHeight());
        if (title == null) return 0;
        Font f = Font.getFont(Font.FACE_SYSTEM, Font.STYLE_BOLD, Font.SIZE_MEDIUM);
        g.setColor(0x003366);
        g.fillRect(0, 0, Display.WIDTH, f.getHeight() + 4);
        g.setColor(0xffffff);
        g.setFont(f);
        g.drawString(title, Display.WIDTH / 2, 2, Graphics.TOP | Graphics.HCENTER);
        return f.getHeight() + 4;
    }
}
