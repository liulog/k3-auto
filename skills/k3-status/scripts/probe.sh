#!/usr/bin/env bash
# Read-only Linux snapshot, with no environment-specific defaults.
address="${1:-}"
board="${2:-}"
serial="${3:?serial device required}"
supplied_log="${4:-}"

# 板卡 SSH 方式：有 helper 用 helper（可密码认证），否则用只读 key 认证。
if test -n "${K3_BOARD_SSH_HELPER:-}"; then
    board_ssh() { python3 "$K3_BOARD_SSH_HELPER" "$1" "$2"; }
else
    board_ssh() {
        ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=5 \
            -o ConnectionAttempts=1 "$1" "$2"
    }
fi
printf '\n[HOST_TIME]\n'
date -Is
printf '\n[SERIAL_DEVICE]\n'
ls -l -- "$serial" 2>&1 || true
printf '\n[SERIAL_OWNER_FUSER]\n'
if command -v fuser >/dev/null; then fuser -v "$serial" 2>&1 || true; else echo 'fuser unavailable'; fi
printf '\n[HOST_RUNNERS_AND_SERIAL]\n'
ps -eo pid,ppid,etime,args | awk '/minicom|picocom|screen|k3_.*(runner|controller|lmbench)|make rerun/ && !/awk/ && !/screensaver/ {print}'
read_log() {
    printf 'log_source=%s log=%s\n' "$1" "$2"
    if test -r "$2"; then
        stat -c 'log_size=%s log_mtime=%y' -- "$2"
        grep -aE 'Filename |Bytes transferred|Linux version |U-Boot |Starting kernel' -- "$2" | tail -n 12
    else echo 'log_unreadable_or_missing'; fi
}
printf '\n[BOOT_LOG_OBSERVATIONS_NOT_VERIFIED_IMAGE_IDENTITY]\n'
if test -n "$supplied_log"; then
    read_log supplied "$supplied_log"
else
    ps -eo args= | awk -v device="$serial" '
    $1 ~ /(^|\/)minicom$/ {selected=0; logpath="";
      for(i=1;i<NF;i++) {if($i=="-D" && $(i+1)==device) selected=1; if($i=="-C") logpath=$(i+1)}
      if(selected && logpath!="") print logpath}' |
    while IFS= read -r logfile; do read_log active_collector "$logfile"; done
fi
printf '\n[PING]\n'
if test -z "$address"; then echo 'not requested';
elif ping -c 1 -W 2 "$address" >/dev/null 2>&1; then echo 'reachable (boot phase unknown)';
else echo 'unreachable (does not prove power-off)'; fi
printf '\n[BOARD_SSH_READ_ONLY]\n'
if test -z "$board"; then echo 'not requested; kernel/test unknown'; exit 0; fi
board_ssh "$board" '
echo KERNEL; uname -a
echo CMDLINE; cat /proc/cmdline
echo CONFIG; zcat /proc/config.gz 2>/dev/null | grep -E "^(CONFIG=|# CONFIG is not set)"
echo BENCHMARK_PROCESSES
ps -eo pid,ppid,etime,args | grep -E "make rerun|/bin/sh ./lmbench|UnixBench/Run|UnixBench/pgms/|(^|[[:space:]/])(lat_|bw_)[a-z0-9_]+|k3_.*evidence" | grep -v -E "grep -E|bash -c" | head -n 35
echo MEMORY; free -h
' </dev/null
rc=$?
if test "$rc" -ne 0; then printf 'board_ssh_failed=%s; kernel/test unknown\n' "$rc"; fi
