"""
The module implementing Keyword, Section and KeywordStore classes,
used to declared node and service configuration keywords and their
properties.
"""
from __future__ import print_function
import os
import copy
from textwrap import TextWrapper

from rcGlobalEnv import rcEnv
import rcExceptions as ex

class MissKeyNoDefault(Exception):
    pass

class KeyInvalidValue(Exception):
    pass

class Keyword(object):
    def __init__(self, section, keyword,
                 rtype=None,
                 required=False,
                 generic=False,
                 at=False,
                 inheritance="leaf > head",
                 scope_order="specific > generic",
                 default=None,
                 default_text=None,
                 candidates=None,
                 strict_candidates=True,
                 convert=None,
                 depends=[],
                 text="",
                 example=None,
                 provisioning=False):
        self.section = section
        self.keyword = keyword
        if rtype is None or isinstance(rtype, list):
            self.rtype = rtype
        else:
            self.rtype = [rtype]
        self.generic = generic
        self.at = at
        self.top = None
        self.required = required
        self.default = default
        self.default_text = default_text
        self.candidates = candidates
        self.strict_candidates = strict_candidates
        self.depends = depends
        self.text = text
        self.provisioning = provisioning
        self.convert = convert
        self.inheritance = inheritance
        self.scope_order = scope_order
        if example is not None:
            self.example = example
        elif self.convert == "size":
            self.example = "100m"
        elif self.convert == "duration":
            self.example = "1h"
        else:
            self.example = "foo"

        if self.default_text is None:
            self.default_text = self.default

    def __lt__(self, o):
        return self.section + self.keyword < o.section + o.keyword

    def __getattribute__(self, attr):
        if attr == "default":
            return copy.copy(object.__getattribute__(self, attr))
        return object.__getattribute__(self, attr)

    def deprecated(self):
        if self.keyword in self.top.deprecated_keywords:
            return True
        if self.rtype is None:
            return self.section+"."+self.keyword in self.top.deprecated_keywords
        for rtype in self.rtype:
            if rtype is None:
                if self.section+"."+self.keyword in self.top.deprecated_keywords:
                    return True
            elif self.section+"."+rtype+"."+self.keyword in self.top.deprecated_keywords:
                return True
        return False

    def template(self, fmt="text", section=None):
        if self.deprecated():
            return ''

        if fmt == "text":
            return self.template_text()
        elif fmt == "rst":
            return self.template_rst(section=section)
        else:
            return ""

    def template_text(self):
        wrapper = TextWrapper(subsequent_indent="#%18s"%"", width=78)

        depends = " && ".join(["%s in %s"%(d[0], d[1]) for d in self.depends])
        if depends == "":
            depends = None

        if isinstance(self.candidates, (list, tuple, set)):
            candidates = " | ".join([str(x) for x in self.candidates])
        else:
            candidates = str(self.candidates)
        if not self.strict_candidates:
            candidates += " ..."

        s = '#\n'
        s += "# keyword:          %s\n"%self.keyword
        s += "# ----------------------------------------------------------------------------\n"
        s += "#  scopable:        %s\n"%str(self.at)
        s += "#  required:        %s\n"%str(self.required)
        if self.top.has_default_section:
            s += "#  provisioning:    %s\n"%str(self.provisioning)
        s += "#  default:         %s\n"%str(self.default_text)
        if self.top.has_default_section:
            s += "#  inheritance:     %s\n"%str(self.inheritance)
        s += "#  scope order:     %s\n"%str(self.scope_order)
        if self.candidates:
            s += "#  candidates:      %s\n"%candidates
        if depends:
            s += "#  depends:         %s\n"%depends
        if self.convert:
            s += "#  convert:         %s\n"%str(self.convert)
        s += '#\n'
        if self.text:
            wrapper = TextWrapper(subsequent_indent="#%9s"%"", width=78)
            s += wrapper.fill("#  desc:  "+self.text) + "\n"
        s += '#\n'
        if self.default_text is not None:
            val = self.default_text
        elif self.candidates and len(self.candidates) > 0:
            val = self.candidates[0]
        else:
            val = self.example
        s += ";" + self.keyword + " = " + str(val) + "\n\n"
        return s

    def template_rst(self, section=None):
        depends = " && ".join(["%s in %s"%(d[0], d[1]) for d in self.depends])
        if depends == "":
            depends = None

        if isinstance(self.candidates, (list, tuple, set)):
            candidates = " | ".join([str(x) for x in self.candidates])
        else:
            candidates = str(self.candidates)
        if not self.strict_candidates:
            candidates += " ..."

        s = ""
        if section:
            fill=""
            if "template.node" in self.top.template_prefix:
                fill="node."
            if "template.cluster" in self.top.template_prefix:
                fill="cluster."
            if "template.secret" in self.top.template_prefix:
                fill="secret."
            if "template.cfg" in self.top.template_prefix:
                fill="cfg."
            s += ".. _%s%s.%s:\n\n" % (fill, section, self.keyword)

        s += ':kw:`%s`\n' % self.keyword
        s += "=" * (len(self.keyword) + 6) + "\n"
        s += "\n"
        s += "================= ================================================================\n"
        s += "**scopable**      %s\n"%str(self.at)
        s += "**required**      %s\n"%str(self.required)
        if self.top.has_default_section:
            s += "**provisioning**  %s\n"%str(self.provisioning)
        s += "**default**       %s\n"%str(self.default_text)
        if self.top.has_default_section:
            s += "**inheritance**   %s\n"%str(self.inheritance)
        s += "**scope order**   %s\n"%str(self.scope_order)
        if self.candidates:
            s += "**candidates**    %s\n"%candidates
        if depends:
            s += "**depends**       %s\n"%depends
        if self.convert:
            s += "**convert**       %s\n"%str(self.convert)
        s += "================= ================================================================\n"
        s += '\n'
        if self.text:
            s += self.text + "\n"
        s += '\n'
        return s

    def dump(self):
        data = {"keyword": self.keyword}
        if self.rtype:
            data["type"] = self.rtype
        if self.at:
            data["at"] = self.at
        if self.required:
            data["required"] = self.required
        if self.candidates:
            data["candidates"] = self.candidates
            data["strict_candidates"] = self.strict_candidates
        if self.default:
            data["default"] = self.default
        if self.default_text:
            data["default_text"] = self.default_text
        if self.inheritance:
            data["inheritance"] = self.inheritance
        if self.scope_order:
            data["scope_order"] = self.scope_order
        if self.provisioning:
            data["provisioning"] = self.provisioning
        if self.depends:
            data["depends"] = self.depends
        if self.convert:
            data["convert"] = self.convert
        else:
            data["convert"] = "string"
        if self.text:
            data["text"] = self.text
        return data

class Section(object):
    def __init__(self, section, top=None):
        self.section = section
        self.top = top
        self.keywords = []

    def __iadd__(self, o):
        if not isinstance(o, Keyword):
            return self
        self.keywords.append(o)
        return self

    def dump(self):
        data = []
        for kw in self.keywords:
            data.append(kw.dump())
        return data

    def template(self, fmt="text", write=False):
        k = self.getkey("type")
        if k is None:
            return self._template(fmt=fmt, write=write)
        if k.candidates is None:
            return self._template(fmt=fmt, write=write)
        s = ""
        if not k.strict_candidates:
            s += self._template(fmt=fmt, write=write)
        for t in k.candidates:
            s += self._template(t, fmt=fmt, write=write)
        return s

    def _template(self, rtype=None, fmt="text", write=False):
        section = self.section
        if self.section in self.top.deprecated_sections:
            return ""
        if rtype and self.top and self.section+"."+rtype in self.top.deprecated_sections:
            return ""
        if fmt == "text":
            return self._template_text(rtype, section, write=write)
        elif fmt == "rst":
            return self._template_rst(rtype, section, write=write)
        else:
            return ""

    def _template_text(self, rtype, section, write=False):
        fpath = os.path.join(rcEnv.paths.pathdoc, self.top.template_prefix+section+".conf")
        if rtype:
            section += ", type "+rtype
            fpath = os.path.join(rcEnv.paths.pathdoc, self.top.template_prefix+self.section+"."+rtype+".conf")
        s = "#"*78 + "\n"
        s += "# %-74s #\n" % " "
        s += "# %-74s #\n" % section
        s += "# %-74s #\n" % " "
        s += "#"*78 + "\n\n"
        if section in self.top.base_sections:
            s += "[%s]\n" % self.section
        else:
            s += "[%s#rindex]\n" % self.section
        if rtype is not None:
            s += ";type = " + rtype + "\n\n"
            for keyword in sorted(self.getkeys(rtype)):
                s += keyword.template(fmt="text")
        for keyword in sorted(self.getprovkeys(rtype)):
            s += keyword.template(fmt="text")
        for keyword in sorted(self.getkeys()):
            if keyword.keyword == "type":
                continue
            s += keyword.template(fmt="text")
        if write:
            print("write", fpath)
            with open(fpath, "w") as f:
                f.write(s)
        return s

    def _template_rst(self, rtype, section, write=False):
        dpath = os.path.join(rcEnv.paths.pathtmp, "rst")
        if not os.path.exists(dpath):
            os.makedirs(dpath)
        if rtype:
            section += "."+rtype
            fpath = os.path.join(dpath, self.top.template_prefix+self.section+"."+rtype+".rst")
        else:
            fpath = os.path.join(dpath, self.top.template_prefix+section+".rst")
        s = section + "\n"
        s += "*" * len(section) + "\n\n"
        if self.top.template_prefix != "template.node." and self.top.template_prefix != "template.cluster." and len(section.split('.')) > 1:
            s += ".. include:: template.service." + section + ".example\n\n"
        for keyword in sorted(self.getkeys(rtype)):
            s += keyword.template(fmt="rst", section=section)
        for keyword in sorted(self.getprovkeys(rtype)):
            s += keyword.template(fmt="rst", section=section)
        if rtype is not None:
            for keyword in sorted(self.getkeys()):
                if keyword.keyword == "type":
                    continue
                s += keyword.template(fmt="rst", section=section)
        if write:
            print("write", fpath)
            with open(fpath, "w") as f:
                f.write(s)
        return s

    def getkeys(self, rtype=None):
        if rtype is None:
            return [k for k in self.keywords if k.rtype is None and not k.provisioning]
        else:
            return [k for k in self.keywords if k.rtype and rtype in k.rtype and not k.provisioning]

    def getprovkeys(self, rtype=None):
        if rtype is None:
            return [k for k in self.keywords if k.rtype is None and k.provisioning]
        else:
            return [k for k in self.keywords if k.rtype and rtype in k.rtype and k.provisioning]

    def getkey(self, keyword, rtype=None):
        if '@' in keyword:
            l = keyword.split('@')
            if len(l) != 2:
                return
            keyword, _ = l
        if rtype:
            fkey = ".".join((self.section, rtype, keyword))
            if self.top is not None and fkey in self.top.deprecated_keywords:
                keyword = self.top.deprecated_keywords[fkey]
                if keyword is None:
                    return
            for k in self.keywords:
                if k.keyword != keyword:
                    continue
                if isinstance(k.rtype, (tuple, list)) and rtype in k.rtype:
                    return k
                if rtype == k.rtype:
                    return k
                if k.rtype is None:
                    return k
        else:
            fkey = ".".join((self.section, keyword))
            if self.top is not None and fkey in self.top.deprecated_keywords:
                keyword = self.top.deprecated_keywords[fkey]
            for k in self.keywords:
                if k.keyword != keyword:
                    continue
                if isinstance(k.rtype, (tuple, list)) and None in k.rtype:
                    return k
                if k.rtype is None:
                    return k
        return

class KeywordStore(dict):
    def __init__(self, provision=False, keywords=[], deprecated_keywords={},
                 deprecated_sections={}, template_prefix="template.",
                 base_sections=[], has_default_section=True):
        dict.__init__(self)
        self.sections = {}
        self.deprecated_sections = deprecated_sections
        self.deprecated_keywords = deprecated_keywords
        self.template_prefix = template_prefix
        self.base_sections = base_sections
        self.provision = provision
        self.has_default_section = has_default_section

        for keyword in keywords:
            sections = keyword.get("sections", [keyword.get("section")])
            prefixes = keyword.get("prefixes", [""])
            for section in sections:
                for prefix in prefixes:
                    data = dict((key, val) for (key, val) in keyword.items() if key not in ("sections", "prefixes"))
                    data.update({
                        "section": section,
                        "keyword": prefix+keyword["keyword"],
                        "text": keyword["text"].replace("{prefix}", prefix),
                    })
                    self += Keyword(**data)

    def __iadd__(self, o):
        if not isinstance(o, Keyword):
            return self
        o.top = self
        if o.section not in self.sections:
            self.sections[o.section] = Section(o.section, top=self)
        self.sections[o.section] += o
        return self

    def __getattr__(self, key):
        return self.sections[str(key)]

    def __getitem__(self, key):
        k = str(key)
        if k not in self.sections:
            return Section(k, top=self)
        return self.sections[k]

    def dump(self):
        data = {}
        for section in sorted(self.sections):
            data[section] = self.sections[section].dump()
        return data

    def print_templates(self, fmt="text"):
        """
        Print templates in the spectified format (text by default, or rst).
        """
        for section in sorted(self.sections):
            print(self.sections[section].template(fmt=fmt))

    def write_templates(self, fmt="text"):
        """
        Write templates in the spectified format (text by default, or rst).
        """
        for section in sorted(self.sections):
            self.sections[section].template(fmt=fmt, write=True)

    def required_keys(self, section, rtype=None):
        """
        Return the list of required keywords in the section for the resource
        type specified by <rtype>.
        """
        if section not in self.sections:
            return []
        return [k for k in sorted(self.sections[section].getkeys(rtype)) if k.required is True]

    def optional_keys(self, section, rtype=None):
        """
        Return the list of optional keywords in the section for the resource
        type specified by <rtype>.
        """
        if section not in self.sections:
            return []
        return [k for k in sorted(self.sections[section].getkeys(rtype)) if k.required is False]

    def all_keys(self, section, rtype=None):
        """
        Return the list of optional keywords in the section for the resource
        type specified by <rtype>.
        """
        if section not in self.sections:
            return []
        return sorted(self.sections[section].getkeys(rtype))

    def section_kwargs(self, cat, rtype=None):
        kwargs = {}
        for keyword in self.all_keys(cat, rtype):
            try:
                kwargs[keyword.name] = self.conf_get(cat, keyword.name)
            except ex.RequiredOptNotFound:
                raise
            except ex.OptNotFound as exc:
                kwargs[keyword.name] = exc.default
        return kwargs

    def purge_keywords_from_dict(self, d, section):
        """
        Remove unknown keywords from a section.
        """
        if section == "env":
            return d
        if 'type' in d:
            rtype = d['type']
        else:
            rtype = None
        delete_keywords = []
        for keyword in d:
            key = self.sections[section].getkey(keyword)
            if key is None and rtype is not None:
                key = self.sections[section].getkey(keyword, rtype)
            if key is None:
                if keyword != "rtype":
                    print("Remove unknown keyword '%s' from section '%s'"%(keyword, section))
                    delete_keywords.append(keyword)

        for keyword in delete_keywords:
            del d[keyword]

        return d

    def update(self, rid, d):
        """
        Given a resource dictionary, spot missing required keys
        and provide a new dictionary to merge populated by default
        values.
        """
        import copy
        completion = copy.copy(d)

        # decompose rid into section and rtype
        if rid in ('DEFAULT', 'env'):
            section = rid
            rtype = None
        else:
            if '#' not in rid:
                return {}
            l = rid.split('#')
            if len(l) != 2:
                return {}
            section = l[0]
            if 'type' in d:
                rtype = d['type']
            elif self[section].getkey('type') is not None and \
                 self[section].getkey('type').default is not None:
                rtype = self[section].getkey('type').default
            else:
                rtype = None

        # validate command line dictionary
        for keyword, value in d.items():
            if section == "env":
                break
            if section not in self.sections:
                raise KeyInvalidValue("'%s' driver family is not valid in section '%s'"%(section, rid))
            key = self.sections[section].getkey(keyword)
            if key is None and rtype is not None:
                key = self.sections[section].getkey(keyword, rtype)
            if key is None:
                continue
            if key.strict_candidates and key.candidates is not None and value not in key.candidates:
                raise KeyInvalidValue("'%s' keyword has invalid value '%s' in section '%s'"%(keyword, str(value), rid))

        # add missing required keys if they have a known default value
        for key in self.required_keys(section, rtype):
            fkey = ".".join((section, str(rtype), key.keyword))
            if fkey in self.deprecated_keywords:
                continue

            if key.keyword in d:
                continue
            if key.keyword in [x.split('@')[0] for x in d]:
                continue
            if key.default is None:
                raise MissKeyNoDefault("No default value for required key '%s' in section '%s'"%(key.keyword, rid))
            print("Implicitely add [%s] %s = %s" % (rid, key.keyword, str(key.default)))
            completion[key.keyword] = key.default

        # purge unknown keywords and provisioning keywords
        completion = self.purge_keywords_from_dict(completion, section)

        return completion
