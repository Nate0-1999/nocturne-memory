#!/bin/zsh
# M8UP: rig.sh db | spine-start | spine-stop | app-start | app-stop | driver-start | driver-stop | run <script.js> [timeout]
# Every long-running process keeps a living shell parent (never a bare nohup).
W=$HOME/relay/m8up/walk; BIN=$W/venv/bin
export PATH="$BIN:$W/shim:/usr/bin:/bin:/usr/sbin:/sbin"
export DOCKER_HOST=unix://$HOME/.colima/default/docker.sock
SP=8178; AP=8878; DP=9678
held() { lsof -tiTCP:$1 -sTCP:LISTEN >/dev/null; }
stop() { pid=$(lsof -tiTCP:$1 -sTCP:LISTEN); [ -n "$pid" ] && kill $pid; i=0
  while held $1; do sleep 0.5; i=$((i+1)); [ $i -eq 20 ] && kill -INT $pid; done; echo "port $1 free"; }  # the driver once ignored TERM
case "$1" in
  db) $BIN/python $W/rig.py db ;;
  spine-start) held $SP && { echo "port $SP is held"; exit 4; }
    nohup sh -c "$BIN/python $W/rig.py spine >> $W/logs/spine.log 2>&1; echo spine-exited >> $W/logs/spine.log" >/dev/null 2>&1 &
    until held $SP; do sleep 0.5; done; echo "palace on $SP" ;;
  spine-stop) stop $SP ;;
  app-start) held $AP && { echo "port $AP is held"; exit 4; }
    cd $W/project && nohup sh -c "$BIN/python $W/rig.py app >> $W/logs/app.log 2>&1; echo app-exited >> $W/logs/app.log" >/dev/null 2>&1 < /dev/null &
    i=0; until curl -sf http://127.0.0.1:$AP/v1/model-policies >/dev/null; do sleep 1; i=$((i+1)); [ $i -gt 90 ] && { echo "app did not start"; exit 5; }; done
    curl -s -o /dev/null -X PUT -H 'Content-Type: application/json' -d '{"policy":"pinned:openrouter:openai/gpt-4.1-mini"}' http://127.0.0.1:$AP/v1/model-policies/curator; echo "app on $AP" ;;
  app-stop) stop $AP ;;
  driver-start) held $DP && { echo "port $DP is held"; exit 4; }
    cd $W && WALK_ROOT=$W WALK_BASE=http://127.0.0.1:$AP DRIVER_PORT=$DP DRIVER_PROFILE=${2:-m8up-driver-profile} nohup sh -c "node $W/driver.mjs >> $W/logs/driver.log 2>&1; echo driver-exited >> $W/logs/driver.log" >/dev/null 2>&1 &
    until held $DP; do sleep 0.5; done; echo "driver on $DP" ;;
  driver-stop) stop $DP ;;
  run) curl -s --max-time ${3:-900} -X POST --data-binary @"$2" http://127.0.0.1:$DP/run ;;
esac
