# Makefile at top of application tree
TOP = ..
include $(TOP)/configure/CONFIG

# Directories to be built, in any order.
# You can replace these wildcards with an explicit list
# (verified: $(wildcard db* *Db*) already matches "Db" -- `*Db*` matches with an empty prefix --
# confirmed with a throwaway `make -f` test printing DIRS from this directory; no explicit
# "DIRS += Db" is needed for the generated ioc/tools/gen_db.py output under Db/.)
DIRS += $(wildcard src* *Src*)
DIRS += $(wildcard db* *Db*)

# If the build order matters, add dependency rules like this,
# which specifies that xxxSrc must be built after src:
#xxxSrc_DEPEND_DIRS += src

include $(TOP)/configure/RULES_DIRS
