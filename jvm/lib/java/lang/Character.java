package java.lang;

public final class Character {
    public static final int MIN_RADIX = 2;
    public static final int MAX_RADIX = 36;
    public static final char MIN_VALUE = '\u0000';
    public static final char MAX_VALUE = '￿';
    private final char value;

    public Character(char value) {
        this.value = value;
    }

    public char charValue() { return value; }
    public static Character valueOf(char c) { return new Character(c); }
    public int hashCode() { return value; }
    public boolean equals(Object o) { return o instanceof Character && ((Character) o).value == value; }
    public String toString() { return String.valueOf(value); }

    public static boolean isLowerCase(char c) {
        return (c >= 'a' && c <= 'z') || (c >= 'ß' && c <= 'ÿ' && c != '÷') || (c >= 'а' && c <= 'џ');
    }

    public static boolean isUpperCase(char c) {
        return (c >= 'A' && c <= 'Z') || (c >= 'À' && c <= 'Þ' && c != '×') || (c >= 'Ѐ' && c <= 'Я');
    }

    public static boolean isDigit(char c) {
        return c >= '0' && c <= '9';
    }

    public static boolean isLetter(char c) {
        return isLowerCase(c) || isUpperCase(c);
    }

    public static boolean isLetterOrDigit(char c) {
        return isLetter(c) || isDigit(c);
    }

    public static boolean isSpace(char c) {
        return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f';
    }

    public static boolean isWhitespace(char c) {
        return isSpace(c);
    }

    public static char toLowerCase(char c) {
        if (c >= 'A' && c <= 'Z') return (char) (c + 32);
        if (c >= 'À' && c <= 'Þ' && c != '×') return (char) (c + 32);
        if (c >= 'А' && c <= 'Я') return (char) (c + 32);
        if (c >= 'Ѐ' && c <= 'Џ') return (char) (c + 80);
        return c;
    }

    public static char toUpperCase(char c) {
        if (c >= 'a' && c <= 'z') return (char) (c - 32);
        if (c >= 'à' && c <= 'þ' && c != '÷') return (char) (c - 32);
        if (c >= 'а' && c <= 'я') return (char) (c - 32);
        if (c >= 'ѐ' && c <= 'џ') return (char) (c - 80);
        return c;
    }

    public static int digit(char c, int radix) {
        int d;
        if (c >= '0' && c <= '9') d = c - '0';
        else if (c >= 'a' && c <= 'z') d = c - 'a' + 10;
        else if (c >= 'A' && c <= 'Z') d = c - 'A' + 10;
        else return -1;
        return d < radix ? d : -1;
    }

    public static char forDigit(int digit, int radix) {
        if (digit < 0 || digit >= radix || radix < MIN_RADIX || radix > MAX_RADIX) return '\u0000';
        return (char) (digit < 10 ? '0' + digit : 'a' + digit - 10);
    }
}
