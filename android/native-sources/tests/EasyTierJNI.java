package com.easytier.jni;

public final class EasyTierJNI {
    static { System.loadLibrary("easytier_jni"); }
    public static native int parseConfig(String config);
    public static native int runNetworkInstance(String config);
    public static native int retainNetworkInstance(String[] names);
    public static native int stopAllInstances();
    public static native String collectNetworkInfos(int maxLength);
    public static native String getLastError();

    private static int checks;
    private static void check(boolean condition) {
        if (!condition) throw new AssertionError("Check " + (checks + 1) + " failed");
        ++checks;
    }

    public static void main(String[] args) throws Exception {
        check(getLastError() == null);
        check(parseConfig("Computador Ação 🖥") == 0);
        check(parseConfig(null) == -1);
        check(getLastError() != null);
        check(parseConfig("x\u0000y") == -1);
        check(parseConfig("\ud800") == -1);
        check(parseConfig("\udfff") == -1);
        check(parseConfig("x".repeat(131073)) == -1);
        check(parseConfig("invalid=secret-pairing-value") == -1);
        check(!getLastError().contains("secret-pairing-value"));
        check(runNetworkInstance("run=valid") == 0);
        check(getLastError() == null);
        check(runNetworkInstance("run=invalid") == -1);
        check(retainNetworkInstance(new String[]{"α", "🖥"}) == 0);
        check(retainNetworkInstance(new String[]{null}) == -1);
        check(retainNetworkInstance(null) == 0);
        check(retainNetworkInstance(new String[0]) == 0);
        check(retainNetworkInstance(new String[101]) == -1);
        check(stopAllInstances() == 0);
        check(collectNetworkInfos(0) == null);
        check(collectNetworkInfos(101) == null);
        check(parseConfig("mode=normal") == 0);
        String expected = "{\"map\":{\"PC \\\"α\\\"\\u000a🖥\":{\"running\":true,\"hostname\":\"Ação 🖥\"}}}";
        for (int i = 0; i < 50; ++i) check(expected.equals(collectNetworkInfos(100)));
        check(parseConfig("mode=partial") == 0);
        check(collectNetworkInfos(100) == null);
        check(getLastError() != null);
        check(parseConfig("mode=normal") == 0);
        check(expected.equals(collectNetworkInfos(1)));
        check(parseConfig("mode=empty") == 0);
        check("{\"map\":{}}".equals(collectNetworkInfos(100)));
        check(parseConfig("mode=normal") == 0);
        Thread[] workers = new Thread[8];
        final Throwable[] errors = new Throwable[8];
        for (int i = 0; i < workers.length; ++i) {
            final int index = i;
            workers[i] = new Thread(() -> {
                try {
                    for (int n = 0; n < 100; ++n) {
                        if (!expected.equals(collectNetworkInfos(100))) throw new AssertionError("concurrent status");
                    }
                } catch (Throwable t) { errors[index] = t; }
            });
            workers[i].start();
        }
        for (int i = 0; i < workers.length; ++i) { workers[i].join(); check(errors[i] == null); }
        System.out.println("JNI boundary checks passed: " + checks + "; concurrent calls: 800");
    }
}
