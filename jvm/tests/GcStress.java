/* Allocation churn with long-lived survivors; run with a small heap. */
public class GcStress {
    static class Node {
        final int value;
        Node next;
        Node(int value, Node next) { this.value = value; this.next = next; }
    }

    static Node keep;
    static String[] kept = new String[64];

    public static void main(String[] args) {
        long checksum = 0;
        for (int round = 0; round < 200; round++) {
            Node list = null;
            for (int i = 0; i < 500; i++) list = new Node(i * round, list);
            int sum = 0;
            for (Node n = list; n != null; n = n.next) sum += n.value;
            checksum += sum;
            if (round % 10 == 0) keep = new Node(round, keep);
            String s = "round" + round + ":" + sum;
            kept[round % kept.length] = s;
            int[] junk = new int[1000 + round];
            junk[junk.length - 1] = round;
            checksum += junk[junk.length - 1];
        }
        int survivors = 0;
        for (Node n = keep; n != null; n = n.next) survivors += n.value;
        System.out.println(checksum + " " + survivors);
        for (int i = 0; i < 3; i++) System.out.println(kept[i]);
        StringBuffer text = new StringBuffer();
        for (int i = 0; i < 2000; i++) text.append((char) ('a' + i % 26));
        System.out.println(text.length() + " " + text.toString().hashCode());
    }
}
