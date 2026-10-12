# Build aur-gate multi-call admission gate binary
all: aur-gate

aur-gate: src/main.rs src/resources.rs ../server/libaur.rs
	rustc -O -C opt-level=z -C panic=abort -C lto=fat -C strip=symbols -C codegen-units=1 src/main.rs -o aur-gate

clean:
	rm -f aur-gate

.PHONY: all clean
