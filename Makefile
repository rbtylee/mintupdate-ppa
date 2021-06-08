all: buildmo

buildmo:
	@echo "Building the mo files"
	@for FILE in $$(ls po/*.po); do \
		PROJECT=$${FILE##*/};PROJECT=$${PROJECT%-*};\
		LANG=$${FILE%.po};LANG=$${LANG#*$${PROJECT}-};\
		install -d usr/share/locale/$${LANG}/LC_MESSAGES/; \
		msgfmt -o usr/share/locale/$${LANG}/LC_MESSAGES/$${PROJECT}.mo $${FILE}; \
	done \

clean:
	rm -rf usr/share/locale
