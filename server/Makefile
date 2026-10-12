# Build pure Rust native accelerators for AUR server
all: libaur.so

libaur.so: libaur.rs
	rustc --crate-type cdylib -O libaur.rs -o libaur.so

clean:
	rm -f libaur.so

.PHONY: all clean
