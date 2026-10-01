#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <poll.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <unistd.h>

#define IOCTL_MAGIC 'o'

struct cp_image {
	unsigned long long binary;
	uint32_t size;
	uint32_t m_offset;
	uint32_t b_offset;
	uint32_t mode;
	uint32_t len;
} __attribute__((packed));

enum cp_boot_mode {
	CP_BOOT_MODE_NORMAL = 0,
	CP_BOOT_MODE_DUMP = 1,
	CP_BOOT_RE_INIT = 2,
	CP_BOOT_MODE_SILENT = 3,
	CP_BOOT_REQ_CP_RAM_LOGGING = 5,
	CP_BOOT_MODE_MANUAL = 7,
	CP_BOOT_EXT_BAAW = 11,
};

struct boot_mode {
	enum cp_boot_mode idx;
};

#define IOCTL_POWER_ON			_IO(IOCTL_MAGIC, 0x19)
#define IOCTL_POWER_OFF			_IO(IOCTL_MAGIC, 0x20)
#define IOCTL_START_CP_BOOTLOADER	_IOW(IOCTL_MAGIC, 0x22, struct boot_mode)
#define IOCTL_COMPLETE_NORMAL_BOOTUP	_IO(IOCTL_MAGIC, 0x23)
#define IOCTL_GET_CP_STATUS		_IO(IOCTL_MAGIC, 0x27)
#define IOCTL_LOAD_CP_IMAGE		_IOW(IOCTL_MAGIC, 0x40, struct cp_image)
#define IOCTL_GET_CP_BOOTLOG		_IO(IOCTL_MAGIC, 0x47)
#define IOCTL_CLR_CP_BOOTLOG		_IO(IOCTL_MAGIC, 0x48)

#define TOC_ENTRY_SIZE	32
#define TOC_MAX_ENTRIES	16
#define SPI_MAX_CHUNK	(128 * 1024)

/*
 * std_udl boot protocol on /dev/umts_boot0, as spoken by stock cbd
 * (shannon_normal_boot -> std_boot_dload). Every request is a 4-byte code
 * written to the node; the bootloader answers with a 4-byte code. With
 * s = stage << 4, stage being the TOC index of the section:
 *
 *   start   0xA100|s  -> 0xC100|s
 *   data    frame {u16 0xA10B|s, u16 len+8, u32 total, u32 offset, u8 data[len]}
 *                     -> 0xC10B|s          (len = 0xC000, last one shorter)
 *   crc     {u32 0xA301|s, u32 crc}       -> 0xC300|s
 *   done    0xA00B|s  -> 0xC00B|s
 *   finish  0xA400    -> 0xC400
 */
#define UDL_CHUNK	0xC000
#define UDL_TIMEOUT_MS	3000

struct udl_frame {
	uint16_t cmd;
	uint16_t len;
	uint32_t total;
	uint32_t offset;
	uint8_t data[UDL_CHUNK];
} __attribute__((packed));

struct toc_entry {
	char name[13];
	uint32_t offset;
	uint32_t load_addr;
	uint32_t size;
	uint32_t crc;
	uint32_t id;
};

static uint32_t rd32(const uint8_t *p)
{
	return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
	       ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static int toc_read(const char *path, struct toc_entry *toc, int max)
{
	uint8_t raw[TOC_ENTRY_SIZE * TOC_MAX_ENTRIES];
	FILE *f;
	int n = 0;
	int i;

	f = fopen(path, "rb");
	if (!f) {
		fprintf(stderr, "open %s: %s\n", path, strerror(errno));
		return -1;
	}
	if (fread(raw, 1, sizeof(raw), f) != sizeof(raw)) {
		fprintf(stderr, "%s: short read of TOC\n", path);
		fclose(f);
		return -1;
	}
	fclose(f);

	if (memcmp(raw, "TOC", 3) != 0) {
		fprintf(stderr, "%s: no TOC magic\n", path);
		return -1;
	}

	for (i = 0; i < max && i < TOC_MAX_ENTRIES; i++) {
		const uint8_t *e = raw + i * TOC_ENTRY_SIZE;

		if (e[0] == 0)
			break;

		memcpy(toc[n].name, e, 12);
		toc[n].name[12] = 0;
		toc[n].offset = rd32(e + 12);
		toc[n].load_addr = rd32(e + 16);
		toc[n].size = rd32(e + 20);
		toc[n].crc = rd32(e + 24);
		toc[n].id = rd32(e + 28);
		n++;
	}

	return n;
}

static void toc_print(const struct toc_entry *toc, int n)
{
	int i;

	printf("%-10s %10s %12s %12s %10s %4s\n",
	       "name", "offset", "load_addr", "size", "crc", "id");
	for (i = 0; i < n; i++)
		printf("%-10s %10u   0x%08x %12u 0x%08x %4u\n",
		       toc[i].name, toc[i].offset, toc[i].load_addr,
		       toc[i].size, toc[i].crc, toc[i].id);
}

static const struct toc_entry *toc_find(const struct toc_entry *toc, int n,
					const char *name)
{
	int i;

	for (i = 0; i < n; i++)
		if (strcmp(toc[i].name, name) == 0)
			return &toc[i];

	return NULL;
}

/*
 * NV_NORM, NV_PROT and REPLAY are listed in the TOC with offset 0: they carry
 * no payload in modem.bin, only a load address. The data lives on the device:
 *
 *   NV_NORM  0x50000010  efs            nv_normal.bin
 *   NV_PROT  0x50100000  efs            nv_protected.bin
 *   REPLAY   0x50280000  modem_userdata replay_region.bin
 *
 * All three are 512 KiB, matching the sizes in the TOC.
 */
static const struct {
	const char *section;
	const char *file;
} nv_sources[] = {
	{ "NV_NORM", "nv_normal.bin" },
	{ "NV_PROT", "nv_protected.bin" },
	{ "REPLAY",  "replay_region.bin" },
};

static const char *nv_file_for(const char *section)
{
	size_t i;

	for (i = 0; i < sizeof(nv_sources) / sizeof(nv_sources[0]); i++)
		if (strcmp(nv_sources[i].section, section) == 0)
			return nv_sources[i].file;

	return NULL;
}

static int send_section(int fd, const char *path, const struct toc_entry *e)
{
	uint8_t *buf;
	FILE *f;
	uint32_t sent = 0;

	if (e->size == 0) {
		fprintf(stderr, "%s: zero size\n", e->name);
		return -1;
	}

	f = fopen(path, "rb");
	if (!f) {
		fprintf(stderr, "open %s: %s\n", path, strerror(errno));
		return -1;
	}
	if (fseek(f, e->offset, SEEK_SET) != 0) {
		fprintf(stderr, "seek to %u: %s\n", e->offset, strerror(errno));
		fclose(f);
		return -1;
	}

	buf = malloc(SPI_MAX_CHUNK);
	if (!buf) {
		fclose(f);
		return -1;
	}

	while (sent < e->size) {
		uint32_t chunk = e->size - sent;
		struct cp_image img;

		if (chunk > SPI_MAX_CHUNK)
			chunk = SPI_MAX_CHUNK;

		if (fread(buf, 1, chunk, f) != chunk) {
			fprintf(stderr, "%s: short read at %u\n", e->name, sent);
			free(buf);
			fclose(f);
			return -1;
		}

		memset(&img, 0, sizeof(img));
		img.binary = (unsigned long long)(uintptr_t)buf;
		img.size = chunk;
		img.m_offset = e->load_addr + sent;
		img.b_offset = e->offset + sent;
		img.len = chunk;

		if (ioctl(fd, IOCTL_LOAD_CP_IMAGE, &img) < 0) {
			fprintf(stderr, "IOCTL_LOAD_CP_IMAGE at %u: %s\n",
				sent, strerror(errno));
			free(buf);
			fclose(f);
			return -1;
		}

		sent += chunk;
		printf("\r%s: %u/%u", e->name, sent, e->size);
		fflush(stdout);
	}

	printf("\n");
	free(buf);
	fclose(f);

	return 0;
}

/*
 * Send one TOC section, taking NV data from nvdir rather than from the image.
 * A section with offset 0 that is not one of those has nothing to send, and
 * reading the image at offset 0 would upload the TOC and BOOT instead.
 */
static int send_toc_section(int fd, const char *image, const char *nvdir,
			    const struct toc_entry *e)
{
	const char *nvfile = nv_file_for(e->name);
	struct toc_entry nv;
	struct stat st;
	char path[512];

	if (!nvfile) {
		if (e->offset == 0) {
			fprintf(stderr,
				"%s: offset 0 and no NV source, refusing\n",
				e->name);
			return -1;
		}
		return send_section(fd, image, e);
	}

	snprintf(path, sizeof(path), "%s/%s", nvdir, nvfile);
	if (stat(path, &st) != 0) {
		fprintf(stderr, "%s: %s: %s\n", e->name, path, strerror(errno));
		return -1;
	}
	if ((uint32_t)st.st_size != e->size) {
		fprintf(stderr, "%s: %s is %lld bytes, TOC says %u\n",
			e->name, path, (long long)st.st_size, e->size);
		return -1;
	}

	nv = *e;
	nv.offset = 0;

	printf("%s: from %s\n", e->name, path);

	return send_section(fd, path, &nv);
}

/*
 * mask: bits that must match between resp and exp. 0xffff = exact. Data-frame
 * acks come back as 0xc10f where cbd's decompile said 0xc10b, so frame sends
 * use 0xfff0 (ignore low nibble) and log the actual code.
 */
static int udl_xfer_mask(int fd, uint32_t req, uint32_t exp, uint32_t mask)
{
	uint32_t resp = 0;
	struct pollfd pfd = { .fd = fd, .events = POLLIN };
	int n;

	if (req) {
		if (write(fd, &req, 4) != 4) {
			fprintf(stderr, "udl write 0x%04x: %s\n", req, strerror(errno));
			return -1;
		}
	}
	if (!exp)
		return 0;

	n = poll(&pfd, 1, UDL_TIMEOUT_MS);
	if (n <= 0) {
		fprintf(stderr, "udl req 0x%04x: no response (exp 0x%04x)\n", req, exp);
		return -1;
	}
	n = read(fd, &resp, 4);
	if (n != 4) {
		fprintf(stderr, "udl read: %s (%d)\n", strerror(errno), n);
		return -1;
	}
	if ((resp & mask) != (exp & mask)) {
		fprintf(stderr, "udl req 0x%04x: resp 0x%04x != exp 0x%04x (mask 0x%04x)\n",
			req, resp, exp, mask);
		return -1;
	}
	if (resp != exp)
		fprintf(stderr, "  (accepted resp 0x%04x for exp 0x%04x)\n", resp, exp);
	return 0;
}

static int udl_xfer(int fd, uint32_t req, uint32_t exp)
{
	return udl_xfer_mask(fd, req, exp, 0xffff);
}

static int udl_send_frame(int fd, struct udl_frame *frm, uint32_t exp)
{
	size_t total = 12 + frm->len;
	ssize_t n;

	frm->len += 8;
	n = write(fd, frm, total);
	if (n != (ssize_t)total) {
		fprintf(stderr, "udl frame write: %s (%zd/%zu)\n", strerror(errno), n, total);
		return -1;
	}
	return udl_xfer_mask(fd, 0, exp, 0xfff0);
}

static int dload_section(int fd, const char *path, const struct toc_entry *e,
			 uint32_t stage)
{
	struct udl_frame *frm;
	FILE *f;
	uint32_t s = (stage << 4) & 0xfff0;
	uint32_t sent = 0;

	if (e->size == 0) {
		fprintf(stderr, "%s: zero size\n", e->name);
		return -1;
	}
	f = fopen(path, "rb");
	if (!f) {
		fprintf(stderr, "open %s: %s\n", path, strerror(errno));
		return -1;
	}
	if (fseek(f, e->offset, SEEK_SET) != 0) {
		fprintf(stderr, "seek to %u: %s\n", e->offset, strerror(errno));
		fclose(f);
		return -1;
	}
	frm = malloc(sizeof(*frm));
	if (!frm) {
		fclose(f);
		return -1;
	}

	printf("%s: stage %u start\n", e->name, stage);
	/*
	 * The bootloader needs a few seconds after START_CP_BOOTLOADER before it
	 * answers the very first stage start, and it acks it exactly once. Retry
	 * only for stage 0 (first contact); later stages it is already up.
	 */
	if (stage == 0) {
		int try;

		for (try = 0; try < 15; try++) {
			if (udl_xfer(fd, 0xa100 | s, 0xc100 | s) == 0)
				break;
			sleep(1);
		}
		if (try == 15) {
			fprintf(stderr, "%s: bootloader never acked stage 0 start\n",
				e->name);
			goto fail;
		}
	} else if (udl_xfer(fd, 0xa100 | s, 0xc100 | s) < 0) {
		goto fail;
	}

	while (sent < e->size) {
		uint32_t chunk = e->size - sent;

		if (chunk > UDL_CHUNK)
			chunk = UDL_CHUNK;
		if (fread(frm->data, 1, chunk, f) != chunk) {
			fprintf(stderr, "%s: short read at %u\n", e->name, sent);
			goto fail;
		}
		frm->cmd = 0xa10b | s;
		frm->len = chunk;
		frm->total = e->size;
		frm->offset = sent;
		if (udl_send_frame(fd, frm, 0xc10b | s) < 0)
			goto fail;
		sent += chunk;
		printf("\r%s: %u/%u", e->name, sent, e->size);
		fflush(stdout);
	}
	printf("\n");

	if (e->crc) {
		uint32_t crcreq[2] = { 0xa301 | s, e->crc };

		printf("%s: crc 0x%08x\n", e->name, e->crc);
		if (write(fd, crcreq, 8) != 8) {
			fprintf(stderr, "udl crc write: %s\n", strerror(errno));
			goto fail;
		}
		if (udl_xfer(fd, 0, 0xc300 | s) < 0)
			goto fail;
	}

	if (udl_xfer(fd, 0xa00b | (s & 0x5ff0), 0xc00b | (s & 0x3ff0)) < 0)
		goto fail;
	printf("%s: stage %u done\n", e->name, stage);

	free(frm);
	fclose(f);
	return 0;
fail:
	free(frm);
	fclose(f);
	return -1;
}

/*
 * A section is downloaded over the boot channel unless it is the TOC itself,
 * the BOOT bootloader (already uploaded over SPI) or empty.
 */
static int is_dload_section(const struct toc_entry *e)
{
	if (strcmp(e->name, "TOC") == 0 || strcmp(e->name, "BOOT") == 0)
		return 0;
	if (e->size == 0)
		return 0;
	return 1;
}

/*
 * The bootloader tracks its own download stage counter starting at 0, not the
 * TOC index: it accepts 0xA100 first (proven on device -- 0xA110/0xA120 get no
 * response). So the stage is the position of this section among the downloaded
 * ones (MAIN=0, VSS=1, APM=2, NV_NORM=3, NV_PROT=4, REPLAY=5).
 */
static uint32_t dl_stage(const struct toc_entry *toc, const struct toc_entry *e)
{
	uint32_t stage = 0;
	const struct toc_entry *p;

	for (p = toc; p < e; p++)
		if (is_dload_section(p))
			stage++;
	return stage;
}

static int dload_toc_section(int fd, const char *image, const char *nvdir,
			     const struct toc_entry *toc, int ntoc,
			     const struct toc_entry *e)
{
	const char *nvfile = nv_file_for(e->name);
	struct toc_entry nv;
	struct stat st;
	char path[512];
	uint32_t stage = dl_stage(toc, e);

	(void)ntoc;

	if (!nvfile) {
		if (e->offset == 0) {
			fprintf(stderr, "%s: offset 0 and no NV source, refusing\n",
				e->name);
			return -1;
		}
		return dload_section(fd, image, e, stage);
	}

	snprintf(path, sizeof(path), "%s/%s", nvdir, nvfile);
	if (stat(path, &st) != 0) {
		fprintf(stderr, "%s: %s: %s\n", e->name, path, strerror(errno));
		return -1;
	}
	if ((uint32_t)st.st_size != e->size) {
		fprintf(stderr, "%s: %s is %lld bytes, TOC says %u\n",
			e->name, path, (long long)st.st_size, e->size);
		return -1;
	}
	nv = *e;
	nv.offset = 0;
	printf("%s: from %s\n", e->name, path);
	return dload_section(fd, path, &nv, stage);
}

static int dload_all(int fd, const char *image, const char *nvdir,
		     const struct toc_entry *toc, int ntoc)
{
	int i;

	for (i = 0; i < ntoc; i++) {
		if (!is_dload_section(&toc[i]))
			continue;
		if (dload_toc_section(fd, image, nvdir, toc, ntoc, &toc[i]) != 0)
			return -1;
	}
	return 0;
}

static int complete_bootup(int fd)
{
	int rc = ioctl(fd, IOCTL_COMPLETE_NORMAL_BOOTUP);

	if (rc < 0) {
		fprintf(stderr, "IOCTL_COMPLETE_NORMAL_BOOTUP: %s\n", strerror(errno));
		ioctl(fd, IOCTL_GET_CP_BOOTLOG);
		return -1;
	}
	ioctl(fd, IOCTL_CLR_CP_BOOTLOG);
	printf("normal bootup complete\n");
	return 0;
}

static void usage(const char *argv0)
{
	fprintf(stderr,
		"usage: %s [-d node] [-i modem.bin] [-n nvdir] <command>\n"
		"\n"
		"  toc            list the sections in the image\n"
		"  boot           upload BOOT and start the CP bootloader\n"
		"  upload <NAME>  upload one section by name (IOCTL_LOAD_CP_IMAGE, SPI)\n"
		"  dload <NAME>   send one section over the boot channel (std_udl)\n"
		"  dloadall       send every section except TOC and BOOT (std_udl)\n"
		"  req <hex> <hex> send one std_udl code and expect one (probe)\n"
		"  finish         std_udl finish handshake (0xA400 -> 0xC400)\n"
		"  complete       IOCTL_COMPLETE_NORMAL_BOOTUP\n"
		"  full           boot + dloadall + finish + complete\n"
		"  status         query CP status\n"
		"  poweron        IOCTL_POWER_ON\n"
		"  poweroff       IOCTL_POWER_OFF\n"
		"\n"
		"NV_NORM, NV_PROT and REPLAY come from nvdir, not from the image:\n"
		"  nv_normal.bin and nv_protected.bin from the efs partition,\n"
		"  replay_region.bin from modem_userdata.\n"
		"\n"
		"defaults: -d /dev/umts_boot0  -i ./modem.bin\n"
		"          -n /mnt/nv\n",
		argv0);
}

int main(int argc, char **argv)
{
	const char *node = "/dev/umts_boot0";
	const char *image = "./modem.bin";
	const char *nvdir = "/mnt/nv";
	struct toc_entry toc[TOC_MAX_ENTRIES];
	const char *cmd;
	int ntoc;
	int fd;
	int opt;
	int rc = 1;

	while ((opt = getopt(argc, argv, "d:i:n:h")) != -1) {
		switch (opt) {
		case 'd':
			node = optarg;
			break;
		case 'i':
			image = optarg;
			break;
		case 'n':
			nvdir = optarg;
			break;
		default:
			usage(argv[0]);
			return 1;
		}
	}

	if (optind >= argc) {
		usage(argv[0]);
		return 1;
	}
	cmd = argv[optind];

	ntoc = toc_read(image, toc, TOC_MAX_ENTRIES);
	if (ntoc < 0)
		return 1;

	if (strcmp(cmd, "toc") == 0) {
		toc_print(toc, ntoc);
		return 0;
	}

	fd = open(node, O_RDWR);
	if (fd < 0) {
		fprintf(stderr, "open %s: %s\n", node, strerror(errno));
		return 1;
	}

	if (strcmp(cmd, "status") == 0) {
		int st = ioctl(fd, IOCTL_GET_CP_STATUS);

		if (st < 0)
			fprintf(stderr, "IOCTL_GET_CP_STATUS: %s\n", strerror(errno));
		else
			printf("cp status: %d\n", st);
		rc = (st < 0);
	} else if (strcmp(cmd, "poweron") == 0) {
		rc = ioctl(fd, IOCTL_POWER_ON) < 0;
		if (rc)
			fprintf(stderr, "IOCTL_POWER_ON: %s\n", strerror(errno));
	} else if (strcmp(cmd, "poweroff") == 0) {
		rc = ioctl(fd, IOCTL_POWER_OFF) < 0;
		if (rc)
			fprintf(stderr, "IOCTL_POWER_OFF: %s\n", strerror(errno));
	} else if (strcmp(cmd, "upload") == 0) {
		const struct toc_entry *e;

		if (optind + 1 >= argc) {
			usage(argv[0]);
			goto out;
		}
		e = toc_find(toc, ntoc, argv[optind + 1]);
		if (!e) {
			fprintf(stderr, "no section named %s\n", argv[optind + 1]);
			goto out;
		}
		rc = send_toc_section(fd, image, nvdir, e) != 0;
	} else if (strcmp(cmd, "dload") == 0) {
		const struct toc_entry *e;

		if (optind + 1 >= argc) {
			usage(argv[0]);
			goto out;
		}
		e = toc_find(toc, ntoc, argv[optind + 1]);
		if (!e) {
			fprintf(stderr, "no section named %s\n", argv[optind + 1]);
			goto out;
		}
		rc = dload_toc_section(fd, image, nvdir, toc, ntoc, e) != 0;
	} else if (strcmp(cmd, "dloadall") == 0) {
		rc = dload_all(fd, image, nvdir, toc, ntoc) != 0;
	} else if (strcmp(cmd, "req") == 0) {
		uint32_t req, exp;

		if (optind + 2 >= argc) {
			usage(argv[0]);
			goto out;
		}
		req = strtoul(argv[optind + 1], NULL, 16);
		exp = strtoul(argv[optind + 2], NULL, 16);
		rc = udl_xfer(fd, req, exp) != 0;
		if (!rc)
			printf("req 0x%04x -> 0x%04x ok\n", req, exp);
	} else if (strcmp(cmd, "finish") == 0) {
		rc = udl_xfer(fd, 0xa400, 0xc400) != 0;
		if (!rc)
			printf("finish handshake ok\n");
	} else if (strcmp(cmd, "complete") == 0) {
		rc = complete_bootup(fd) != 0;
	} else if (strcmp(cmd, "full") == 0) {
		const struct toc_entry *e = toc_find(toc, ntoc, "BOOT");
		struct boot_mode mode = { .idx = CP_BOOT_MODE_NORMAL };

		if (!e) {
			fprintf(stderr, "no BOOT section in %s\n", image);
			goto out;
		}
		if (ioctl(fd, IOCTL_POWER_ON) < 0) {
			fprintf(stderr, "IOCTL_POWER_ON: %s\n", strerror(errno));
			goto out;
		}
		printf("cp powered on\n");
		if (send_section(fd, image, e) != 0)
			goto out;
		if (ioctl(fd, IOCTL_START_CP_BOOTLOADER, &mode) < 0) {
			fprintf(stderr, "IOCTL_START_CP_BOOTLOADER: %s\n", strerror(errno));
			goto out;
		}
		printf("bootloader started\n");
		if (dload_all(fd, image, nvdir, toc, ntoc) != 0)
			goto out;
		if (udl_xfer(fd, 0xa400, 0xc400) != 0) {
			fprintf(stderr, "finish handshake failed\n");
			goto out;
		}
		printf("finish handshake ok\n");
		rc = complete_bootup(fd) != 0;
	} else if (strcmp(cmd, "boot") == 0) {
		const struct toc_entry *e = toc_find(toc, ntoc, "BOOT");
		struct boot_mode mode = { .idx = CP_BOOT_MODE_NORMAL };

		if (!e) {
			fprintf(stderr, "no BOOT section in %s\n", image);
			goto out;
		}

		if (ioctl(fd, IOCTL_POWER_ON) < 0) {
			fprintf(stderr, "IOCTL_POWER_ON: %s\n", strerror(errno));
			goto out;
		}
		printf("cp powered on\n");

		if (send_section(fd, image, e) != 0)
			goto out;

		if (ioctl(fd, IOCTL_START_CP_BOOTLOADER, &mode) < 0) {
			fprintf(stderr, "IOCTL_START_CP_BOOTLOADER: %s\n",
				strerror(errno));
			goto out;
		}
		printf("bootloader started\n");
		rc = 0;
	} else {
		usage(argv[0]);
	}

out:
	close(fd);
	return rc;
}
