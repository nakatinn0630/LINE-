#!/usr/bin/env bash
# 現行サイトの画像を assets/original/ に再取得する（manifest.md と同じ37ファイル）
set -euo pipefail
cd "$(dirname "$0")/original"
BASE="https://takezoe.tkcnf.com"
for p in \
  /design/images/bnr-fixed/bnr-fixed-pc3.jpg /design/images/bnr-fixed/bnr-fixed-sp3.jpg \
  /design/images/bnr-fixed/bnr-foodtax-pc1.png /design/images/bnr-fixed/bnr-foodtax-sp1.png \
  /design/images/bnr-fixed/bnr-nensyunokabe-pc.png /design/images/bnr-fixed/bnr-nensyunokabe-sp.png \
  /design/images/common/icon-close.png /design/images/common/icon-open.png /design/images/common/btn-sp-menu.png \
  /library/5656ef252d0fbe3c300c1ef4/6051bfba9fad362961181b79.png /library/5656ef252d0fbe3c300c1ef4/629debfee3c306600710d453.png \
  /library/6376f14539fb706920708a93/66f397a315ebfd1f20e64671.png /library/6376f14539fb706920708a93/66f397a37391051e43d2a644.png \
  /library/6376f14539fb706920708a93/66f397a44986580e5c97d052.png \
  /material/lib02/KFS_01.png /material/lib02/KFS_02.png /material/lib02/KFS_03.png /material/lib02/KFS_04.png /material/lib02/KFS_05.png \
  /material/lib02/temp12_pic01.jpg /material/lib02/temp12_pic02.jpg \
  /material/lib02/tempcommon_p02_02.jpg /material/lib02/tempcommon_p02_03.jpg \
  /material/lib02/tempcommon_p03_01.jpg /material/lib02/tempcommon_p03_02.png /material/lib02/tempcommon_p03_03.png \
  /material/lib02/tempcommon_p04_01.jpg /material/lib02/tempcommon_p04_02.jpg /material/lib02/tempcommon_p04_03.jpg \
  /material/lib02/tempcommon_p04_04.jpg /material/lib02/tempcommon_p04_05.jpg /material/lib02/tempcommon_p05_01.jpg \
  /material/lib02/tkc_logo1.gif \
  /material/lib03/bnr_mutual001_a.png /material/lib03/bnr_mutual002_a.png /material/lib03/bnr_mutual003_a.png \
  /material/lib04/bg-img-B001m.png
do
  curl -fsS -A "Mozilla/5.0" -o "$(basename "$p")" "$BASE$p"
  echo "ok $p"
done
