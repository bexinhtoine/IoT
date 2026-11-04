#ifndef INDICATOR_MODES_H
#define INDICATOR_MODES_H
typedef enum {
    /* Pattern nền (giữ cho tới khi đổi) */
    INDICATOR_IDLE = 0,     /* tắt hết */
    INDICATOR_RUNNING,      /* tat: cho vat */
    INDICATOR_CHECKING,     /* tat: dang phan tich */
    INDICATOR_ESTOP,        /* do sang lien tuc + coi 1 s, den khi reset */
    INDICATOR_FAULT,        /* do sang lien tuc den khi reset */
    INDICATOR_OTA,          /* tat den */
    /* Pattern tạm (chạy xong tự về pattern nền trước đó) */
    INDICATOR_OK,           /* xanh 300 ms, bip 100 ms, roi tat */
    INDICATOR_DEFECT,       /* do 300 ms roi tat */
    INDICATOR_SECURITY,     /* do nhay 3 nhip + 3 bip, roi tat */
    INDICATOR_FAILSAFE,     /* do 300 ms roi tat khi loai fail-safe */
    INDICATOR_NOT_BOX,      /* do 3 nhip 200 ms on/off, roi tat */
    INDICATOR_MODE_COUNT
} indicator_mode_t;
#endif
