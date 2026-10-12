# Build aur-gate multi-call admission gate binary
all: aur-gate

aur-gate: src/main.rs src/resources.rs ../server/libaur.rs
	rustc -O -C opt-level=3 src/main.rs -o aur-gate

clean:
	rm -f aur-gate

.PHONY: all clean
