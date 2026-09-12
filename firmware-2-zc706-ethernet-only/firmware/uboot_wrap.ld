ENTRY(_start)
SECTIONS
{
  . = 0x04000000;
  _start = .;
  .text : { *(.data) }
}
