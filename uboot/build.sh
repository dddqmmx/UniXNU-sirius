#!/bin/sh
# build.sh: build this repository's U-Boot image (boot-unixnu.img) from the pinned
# uboot/src submodule and uboot/unixnu-sirius.env, with CONFIG_CMD_PMIC=y enabled so
# U-Boot can read/write PM660 PON_SOFT_RB_SPARE (0x88f) to enter ABL fastboot.
#
# Needs an aarch64 GCC cross compiler (CROSS_COMPILE) and mkbootimg on PATH.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
src="$here/src"
if [ ! -f "$src/sirius-build.sh" ]; then
	echo "uboot/src is empty: git submodule update --init --depth 1 uboot/src" >&2
	exit 1
fi

OUT=${OUT:-"$here/build"}
IMG=${IMG:-"$here/boot-unixnu.img"}
case "$(uname -m)" in
	aarch64|arm64) DEF_CROSS= ;;
	*)             DEF_CROSS=aarch64-linux-gnu- ;;
esac
CROSS=${CROSS_COMPILE:-$DEF_CROSS}
JOBS=${JOBS:-$(nproc)}
DT=${DEVICE_TREE:-qcom/sdm710-xiaomi-sirius}

mkdir -p "$OUT"
make -C "$src" CROSS_COMPILE="$CROSS" O="$OUT" qcom_defconfig qcom-phone.config sirius.config
"$src/scripts/config" --file "$OUT/.config" -e CMD_PMIC
make -C "$src" CROSS_COMPILE="$CROSS" O="$OUT" olddefconfig
make -C "$src" CROSS_COMPILE="$CROSS" O="$OUT" -j"$JOBS" DEVICE_TREE="$DT" \
	"CONFIG_ENV_DEFAULT_ENV_TEXT_FILE=$here/unixnu-sirius.env"
gzip -c "$OUT/u-boot-nodtb.bin" > "$OUT/u-boot-nodtb.bin.gz"
DTB="$OUT/dts/upstream/src/arm64/qcom/sdm710-xiaomi-sirius.dtb"
cat "$OUT/u-boot-nodtb.bin.gz" "$DTB" > "$OUT/uboot-payload"
mkbootimg --kernel_offset 0x00008000 --pagesize 4096 --kernel "$OUT/uboot-payload" -o "$IMG"
ls -lh "$IMG"
echo "built $IMG"
