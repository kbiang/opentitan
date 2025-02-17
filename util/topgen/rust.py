# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
"""This contains a class which is used to help generate `top_{name}.rs`."""
from collections import OrderedDict, defaultdict
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from mako.template import Template
from reggen.ip_block import IpBlock

from .lib import Name, get_base_and_size

RUST_FILE_EXTENSIONS = (".rs")


class MemoryRegion(object):
    def __init__(self, top_name: Name, name: Name, base_addr: int, size_bytes: int):
        assert isinstance(base_addr, int)
        self.name = top_name + name
        self.short_name = name
        self.base_addr = base_addr
        self.size_bytes = size_bytes
        self.size_words = (size_bytes + 3) // 4

    def base_addr_name(self, short=False):
        if short:
            return self.short_name + Name(["base", "addr"])
        else:
            return self.name + Name(["base", "addr"])

    def offset_name(self, short=False):
        if short:
            return self.short_name + Name(["offset"])
        else:
            return self.name + Name(["offset"])

    def size_bytes_name(self, short=False):
        if short:
            return self.short_name + Name(["size", "bytes"])
        else:
            return self.name + Name(["size", "bytes"])

    def size_words_name(self, short=False):
        if short:
            return self.short_name + Name(["size", "words"])
        else:
            return self.name + Name(["size", "words"])


class RustEnum(object):
    def __init__(self, top_name, name, repr_type=None, derive_list=["Copy", "Clone"]):
        self.name = top_name + name
        self.short_name = name
        self.enum_counter = 0
        self.finalized = False
        self.first_value = None
        self.last_value = None
        self.last_value_docstring = None
        self.repr_type = repr_type
        self.derive_list = derive_list
        self.constants = []
        # todo add flag for doc strings

    def repr(self) -> str:
        if isinstance(self.repr_type, int):
            return "u" + str(self.repr_type)
        elif self.repr_type is None:
            return "u32"
        else:
            return self.repr_type

    def derive(self) -> str:
        if isinstance(self.derive_list, list):
            if len(self.derive_list) > 0:
                return "#[derive({})]\n".format(", ".join(self.derive_list))
        return ""

    def add_constant(self, constant_name, docstring=""):
        assert not self.finalized
        full_name = constant_name
        value = self.enum_counter
        self.enum_counter += 1
        self.constants.append((full_name, value, docstring))
        return full_name

    def add_number_of_variants(self, docstring=""):
        assert not self.finalized
        _, last_val, _ = self.constants[-1]
        self.last_value = last_val + 1
        self.last_value_docstring = docstring
        self.finalized = True

    def calculate_range(self):
        _, last_val, _ = self.constants[-1]
        _, first_val, _ = self.constants[0]
        self.last_value = last_val
        self.first_value = first_val

    def render_host(self, gen_doc=False, gen_name=None):
        self.calculate_range()
        body = ("    pub enum ${enum.short_name.as_rust_type()}: ${enum.repr()} "
                "[default = Self::End] {\n"
                "% for name, value, docstring in enum.constants:\n"
                "        % if len(docstring) > 0  and gen_doc: \n"
                "        /// ${docstring}\n"
                "        % endif \n"
                "        ${name.as_rust_enum()} = ${value},\n"
                "% endfor\n"
                "        End = ${enum.last_value + 1},\n"
                "    }")
        return Template(body).render(enum=self)

    def render(self, gen_range=False, gen_cast=False, derive_list=None):
        if derive_list is not None:
            self.derive_list = derive_list
        self.calculate_range()
        body = ("${enum.derive()}"
                "#[repr(${enum.repr()})]\n"
                "pub enum ${enum.short_name.as_rust_type()} {\n"
                "% for name, value, docstring in enum.constants:\n"
                "    % if len(docstring) > 0 : \n"
                "    /// ${docstring}\n"
                "    % endif \n"
                "    ${name.as_rust_enum()} = ${value},\n"
                "% endfor\n"
                "}")

        impl = ("\n\n"
                "impl ${enum.short_name.as_rust_type()} {\n"
                "    % if enum.last_value_docstring:\n"
                "    /// ${enum.last_value_docstring}\n"
                "    % else: \n"
                "    /// Total number of enum variants \n"
                "    % endif \n"
                "    const NUMBER: usize = ${len(enum.constants)};\n"
                "    /// Enum first valid value\n"
                "    const FIRST: ${enum.repr()} = "
                "Self::${enum.constants[0][0].as_rust_enum()} as ${enum.repr()};\n"
                "    /// Enum last valid value\n"
                "    const LAST: ${enum.repr()} = "
                "Self::${enum.constants[-1][0].as_rust_enum()} as ${enum.repr()};\n"
                "}")

        cast = ("\n\n"
                "impl TryFrom<${enum.repr()}> for "
                "${enum.short_name.as_rust_type()} {\n"
                "    type Error = ${enum.repr()};\n"
                "    fn try_from(val: ${enum.repr()}) -> Result<Self, Self::Error> {\n"
                "        match val {\n"
                "            % for name, value, docstring in enum.constants:\n"
                "            ${value} => Ok(Self::${name.as_rust_enum()}),\n"
                "            % endfor \n"
                "            _ => Err(val),\n"
                "        }\n"
                "    }\n"
                "}")

        if gen_range:
            body += impl
        if gen_cast:
            body += cast
        return Template(body).render(enum=self)


class RustArrayMapping(object):
    def __init__(self, top_name, name, output_type_name):
        self.name = top_name + name
        self.short_name = name
        self.output_type_name = output_type_name

        self.mapping = OrderedDict()

    def add_entry(self, in_name, out_name):
        self.mapping[in_name] = out_name

    def render_definition(self):
        template = ("pub const ${mapping.short_name.as_rust_const()}: "
                    "[${mapping.output_type_name.as_rust_type()}; ${len(mapping.mapping)}] = [\n"
                    "% for in_name, out_name in mapping.mapping.items():\n"
                    "    // ${in_name.as_rust_enum()} ->"
                    " ${mapping.output_type_name.as_rust_type()}::${out_name.as_rust_enum()}\n"
                    "    ${mapping.output_type_name.as_rust_type()}::${out_name.as_rust_enum()},\n"
                    "% endfor\n"
                    "];")
        return Template(template).render(mapping=self)


class RustFileHeader(object):
    def __init__(self, name: str, version_stamp: Dict[str, str], skip: bool):
        self.name = name
        self.data = version_stamp
        self.skip = skip
        tm = int(version_stamp.get('BUILD_TIMESTAMP', 0))
        self.tstamp = datetime.utcfromtimestamp(tm) if tm else datetime.utcnow()

    def build(self) -> str:
        return self.data.get('BUILD_GIT_VERSION', '<unknown>')

    def scm_sha(self) -> str:
        return self.data.get('BUILD_SCM_REVISION', '<unknown>')

    def scm_status(self) -> str:
        return self.data.get('BUILD_SCM_STATUS', '<unknown>')

    def time_stamp(self) -> str:
        return self.tstamp.isoformat()

    def render(self):
        if self.skip:
            return Template(("")).render(header=self)
        else:
            template = ("\n"
                        "// Built for ${header.build()}\n"
                        "// https://github.com/lowRISC/opentitan/tree/${header.scm_sha()}\n"
                        "// Tree status: ${header.scm_status()}\n"
                        "// Build date: ${header.time_stamp()}\n")
            return Template(template).render(header=self)


class TopGenRust:
    def __init__(self, top_info, name_to_block: Dict[str, IpBlock], version_stamp: Dict[str, str]):
        self.top = top_info
        self._top_name = Name(["top"]) + Name.from_snake_case(top_info["name"])
        self._name_to_block = name_to_block
        self.regwidth = int(top_info["datawidth"])
        self.file_header = RustFileHeader("foo.tpl", version_stamp, len(version_stamp) == 0)

        self._init_plic_targets()
        self._init_plic_mapping()
        self._init_alert_mapping()
        self._init_pinmux_mapping()
        self._init_pad_mapping()
        self._init_pwrmgr_wakeups()
        self._init_rstmgr_sw_rsts()
        self._init_pwrmgr_reset_requests()
        self._init_clkmgr_clocks()
        self._init_mmio_region()

    def devices(self) -> List[Tuple[Tuple[str, Optional[str]], MemoryRegion]]:
        '''Return a list of MemoryRegion objects for devices on the bus

        The list returned is pairs (full_if, region) where full_if is itself a
        pair (inst_name, if_name). inst_name is the name of some IP block
        instantiation. if_name is the name of the interface (may be None).
        region is a MemoryRegion object representing the device.

        '''
        ret = []  # type: List[Tuple[Tuple[str, Optional[str]], MemoryRegion]]
        # TODO: This method is invoked in templates, as well as in the extended
        # class TopGenCTest. We could refactor and optimize the implementation
        # a bit.
        self.device_regions = defaultdict(dict)
        for inst in self.top['module']:
            block = self._name_to_block[inst['type']]
            for if_name, rb in block.reg_blocks.items():
                full_if = (inst['name'], if_name)
                full_if_name = Name.from_snake_case(full_if[0])
                if if_name is not None:
                    full_if_name += Name.from_snake_case(if_name)

                name = full_if_name
                base, size = get_base_and_size(self._name_to_block,
                                               inst, if_name)

                region = MemoryRegion(self._top_name, name, base, size)
                self.device_regions[inst['name']].update({if_name: region})
                ret.append((full_if, region))

        return ret

    def memories(self):
        ret = []
        for m in self.top["memory"]:
            ret.append((m["name"],
                        MemoryRegion(self._top_name,
                                     Name.from_snake_case(m["name"]),
                                     int(m["base_addr"], 0),
                                     int(m["size"], 0))))

        for inst in self.top['module']:
            if "memory" in inst:
                for if_name, val in inst["memory"].items():
                    base, size = get_base_and_size(self._name_to_block,
                                                   inst, if_name)

                    name = Name.from_snake_case(val["label"])
                    region = MemoryRegion(self._top_name, name, base, size)
                    ret.append((val["label"], region))

        return ret

    def _init_plic_targets(self):
        enum = RustEnum(self._top_name, Name(["plic", "target"]))

        for core_id in range(int(self.top["num_cores"])):
            enum.add_constant(Name(["ibex", str(core_id)]),
                              docstring="Ibex Core {}".format(core_id))

        enum.add_number_of_variants("Final number of PLIC target")

        self.plic_targets = enum

    def _init_plic_mapping(self):
        """We eventually want to generate a mapping from interrupt id to the
        source peripheral.

        In order to do so, we generate two enums (one for interrupts, one for
        sources), and store the generated names in a dictionary that represents
        the mapping.

        PLIC Interrupt ID 0 corresponds to no interrupt, and so no peripheral,
        so we encode that in the enum as "unknown".

        The interrupts have to be added in order, with "none" first, to ensure
        that they get the correct mapping to their PLIC id, which is used for
        addressing the right registers and bits.
        """
        sources = RustEnum(self._top_name, Name(["plic", "peripheral"]), self.regwidth)
        interrupts = RustEnum(self._top_name, Name(["plic", "irq", "id"]), self.regwidth)
        plic_mapping = RustArrayMapping(
            self._top_name, Name(["plic", "interrupt", "for", "peripheral"]),
            sources.short_name)

        unknown_source = sources.add_constant(Name(["unknown"]),
                                              docstring="Unknown Peripheral")
        none_irq_id = interrupts.add_constant(Name(["none"]),
                                              docstring="No Interrupt")
        plic_mapping.add_entry(none_irq_id, unknown_source)

        # When we generate the `interrupts` enum, the only info we have about
        # the source is the module name. We'll use `source_name_map` to map a
        # short module name to the full name object used for the enum constant.
        source_name_map = {}

        for name in self.top["interrupt_module"]:

            source_name = sources.add_constant(Name.from_snake_case(name),
                                               docstring=name)
            source_name_map[name] = source_name

        sources.add_number_of_variants("Number of PLIC peripheral")

        # Maintain a list of instance-specific IRQs by instance name.
        self.device_irqs = defaultdict(list)
        for intr in self.top["interrupt"]:
            # Some interrupts are multiple bits wide. Here we deal with that by
            # adding a bit-index suffix
            if "width" in intr and int(intr["width"]) != 1:
                for i in range(int(intr["width"])):
                    name = Name.from_snake_case(intr["name"]) + Name([str(i)])
                    irq_id = interrupts.add_constant(name,
                                                     docstring="{} {}".format(
                                                         intr["name"], i))
                    source_name = source_name_map[intr["module_name"]]
                    plic_mapping.add_entry(irq_id, source_name)
                    self.device_irqs[intr["module_name"]].append(intr["name"] +
                                                                 str(i))
            else:
                name = Name.from_snake_case(intr["name"])
                irq_id = interrupts.add_constant(name, docstring=intr["name"])
                source_name = source_name_map[intr["module_name"]]
                plic_mapping.add_entry(irq_id, source_name)
                self.device_irqs[intr["module_name"]].append(intr["name"])

        interrupts.add_number_of_variants("Number of Interrupt ID.")

        self.plic_sources = sources
        self.plic_interrupts = interrupts
        self.plic_mapping = plic_mapping

    def _init_alert_mapping(self):
        """We eventually want to generate a mapping from alert id to the source
        peripheral.

        In order to do so, we generate two enums (one for alerts, one for
        sources), and store the generated names in a dictionary that represents
        the mapping.

        Alert Handler has no concept of "no alert", unlike the PLIC.

        The alerts have to be added in order, to ensure that they get the
        correct mapping to their alert id, which is used for addressing the
        right registers and bits.
        """
        sources = RustEnum(self._top_name, Name(["alert", "peripheral"]), self.regwidth)
        alerts = RustEnum(self._top_name, Name(["alert", "id"]), self.regwidth)
        alert_mapping = RustArrayMapping(
            self._top_name, Name(["alert", "for", "peripheral"]),
            sources.short_name)

        # When we generate the `alerts` enum, the only info we have about the
        # source is the module name. We'll use `source_name_map` to map a short
        # module name to the full name object used for the enum constant.
        source_name_map = {}

        for name in self.top["alert_module"]:
            source_name = sources.add_constant(Name.from_snake_case(name),
                                               docstring=name)
            source_name_map[name] = source_name

        sources.add_number_of_variants("Final number of Alert peripheral")

        self.device_alerts = defaultdict(list)
        for alert in self.top["alert"]:
            if "width" in alert and int(alert["width"]) != 1:
                for i in range(int(alert["width"])):
                    name = Name.from_snake_case(alert["name"]) + Name([str(i)])
                    irq_id = alerts.add_constant(name,
                                                 docstring="{} {}".format(
                                                     alert["name"], i))
                    source_name = source_name_map[alert["module_name"]]
                    alert_mapping.add_entry(irq_id, source_name)
                    self.device_alerts[alert["module_name"]].append(alert["name"] +
                                                                    str(i))
            else:
                name = Name.from_snake_case(alert["name"])
                alert_id = alerts.add_constant(name, docstring=alert["name"])
                source_name = source_name_map[alert["module_name"]]
                alert_mapping.add_entry(alert_id, source_name)
                self.device_alerts[alert["module_name"]].append(alert["name"])

        alerts.add_number_of_variants("The number of Alert ID.")

        self.alert_sources = sources
        self.alert_alerts = alerts
        self.alert_mapping = alert_mapping

    def _init_pinmux_mapping(self):
        """Generate Rust enums for addressing pinmux registers and in/out selects.

        Inputs/outputs are connected in the order the modules are listed in
        the hjson under the "mio_modules" key. For each module, the corresponding
        inouts are connected first, followed by either the inputs or the outputs.

        Inputs:
        - Peripheral chooses register field (pinmux_peripheral_in)
        - Insel chooses MIO input (pinmux_insel)

        Outputs:
        - MIO chooses register field (pinmux_mio_out)
        - Outsel chooses peripheral output (pinmux_outsel)

        Insel and outsel have some special values which are captured here too.
        """
        pinmux_info = self.top['pinmux']
        pinout_info = self.top['pinout']

        # Peripheral Inputs
        peripheral_in = RustEnum(self._top_name, Name(['pinmux', 'peripheral', 'in']),
                                 self.regwidth)
        i = 0
        for sig in pinmux_info['ios']:
            if sig['connection'] == 'muxed' and sig['type'] in ['inout', 'input']:
                index = Name([str(sig['idx'])]) if sig['idx'] != -1 else Name([])
                name = Name.from_snake_case(sig['name']) + index
                peripheral_in.add_constant(name, docstring='Peripheral Input {}'.format(i))
                i += 1

        peripheral_in.add_number_of_variants('Number of peripheral input')

        # Pinmux Input Selects
        insel = RustEnum(self._top_name, Name(['pinmux', 'insel']), self.regwidth)
        insel.add_constant(Name(['constant', 'zero']),
                           docstring='Tie constantly to zero')
        insel.add_constant(Name(['constant', 'one']),
                           docstring='Tie constantly to one')
        i = 0
        for pad in pinout_info['pads']:
            if pad['connection'] == 'muxed':
                insel.add_constant(Name([pad['name']]),
                                   docstring='MIO Pad {}'.format(i))
                i += 1
        insel.add_number_of_variants('Number of valid insel value')

        # MIO Outputs
        mio_out = RustEnum(self._top_name, Name(['pinmux', 'mio', 'out']))
        i = 0
        for pad in pinout_info['pads']:
            if pad['connection'] == 'muxed':
                mio_out.add_constant(Name.from_snake_case(pad['name']),
                                     docstring='MIO Pad {}'.format(i))
                i += 1
        mio_out.add_number_of_variants('Number of valid mio output')

        # Pinmux Output Selects
        outsel = RustEnum(self._top_name, Name(['pinmux', 'outsel']), self.regwidth)
        outsel.add_constant(Name(['constant', 'zero']),
                            docstring='Tie constantly to zero')
        outsel.add_constant(Name(['constant', 'one']),
                            docstring='Tie constantly to one')
        outsel.add_constant(Name(['constant', 'high', 'z']),
                            docstring='Tie constantly to high-Z')
        i = 0
        for sig in pinmux_info['ios']:
            if sig['connection'] == 'muxed' and sig['type'] in ['inout', 'output']:
                index = Name([str(sig['idx'])]) if sig['idx'] != -1 else Name([])
                name = Name.from_snake_case(sig['name']) + index
                outsel.add_constant(name, docstring='Peripheral Output {}'.format(i))
                i += 1

        outsel.add_number_of_variants('Number of valid outsel value')

        self.pinmux_peripheral_in = peripheral_in
        self.pinmux_insel = insel
        self.pinmux_mio_out = mio_out
        self.pinmux_outsel = outsel

    def _init_pad_mapping(self):
        """Generate Rust enums for order of MIO and DIO pads.

        These are needed to configure pad specific configurations such as
        slew rate and other flags.
        """
        direct_enum = RustEnum(self._top_name, Name(["direct", "pads"]))

        muxed_enum = RustEnum(self._top_name, Name(["muxed", "pads"]))

        pads_info = self.top['pinout']['pads']
        muxed = [pad['name'] for pad in pads_info if pad['connection'] == 'muxed']

        # The logic here follows the sequence done in toplevel_pkg.sv.tpl.
        # The direct pads do not enumerate directly from the pinout like the muxed
        # ios.  Instead it follows a direction from the pinmux perspective.
        pads_info = self.top['pinmux']['ios']
        direct = [pad for pad in pads_info if pad['connection'] != 'muxed']

        for pad in (direct):
            name = f"{pad['name']}"
            if pad['width'] > 1:
                name = f"{name}{pad['idx']}"

            direct_enum.add_constant(
                Name.from_snake_case(name))
        direct_enum.add_number_of_variants("Number of valid direct pad")

        for pad in (muxed):
            muxed_enum.add_constant(
                Name.from_snake_case(pad))
        muxed_enum.add_number_of_variants("Number of valid muxed pad")

        self.direct_pads = direct_enum
        self.muxed_pads = muxed_enum

    def _init_pwrmgr_wakeups(self):
        enum = RustEnum(self._top_name, Name(["power", "manager", "wake", "ups"]))

        for signal in self.top["wakeups"]:
            enum.add_constant(
                Name.from_snake_case(signal["module"]) +
                Name.from_snake_case(signal["name"]))

        enum.add_number_of_variants("Number of valid pwrmgr wakeup signal")

        self.pwrmgr_wakeups = enum

    # Enumerates the positions of all software controllable resets
    def _init_rstmgr_sw_rsts(self):
        sw_rsts = self.top['resets'].get_sw_resets()

        enum = RustEnum(self._top_name, Name(["reset", "manager", "sw", "resets"]))

        for rst in sw_rsts:
            enum.add_constant(Name.from_snake_case(rst))

        enum.add_number_of_variants("Number of valid rstmgr software reset request")

        self.rstmgr_sw_rsts = enum

    def _init_pwrmgr_reset_requests(self):
        enum = RustEnum(self._top_name, Name(["power", "manager", "reset", "requests"]))

        for signal in self.top["reset_requests"]["peripheral"]:
            enum.add_constant(
                Name.from_snake_case(signal["module"]) +
                Name.from_snake_case(signal["name"]))

        enum.add_number_of_variants("Number of valid pwrmgr reset_request signal")

        self.pwrmgr_reset_requests = enum

    def _init_clkmgr_clocks(self):
        """
        Creates RustEnums for accessing the software-controlled clocks in the
        design.

        The logic here matches the logic in topgen.py in how it instantiates the
        clock manager with the described clocks.

        We differentiate "gateable" clocks and "hintable" clocks because the
        clock manager has separate register interfaces for each group.
        """
        clocks = self.top['clocks']

        gateable_clocks = RustEnum(self._top_name, Name(["gateable", "clocks"]))
        hintable_clocks = RustEnum(self._top_name, Name(["hintable", "clocks"]))

        c2g = clocks.make_clock_to_group()
        by_type = clocks.typed_clocks()

        for name in by_type.sw_clks.keys():
            # All these clocks start with `clk_` which is redundant.
            clock_name = Name.from_snake_case(name).remove_part("clk")
            docstring = "Clock {} in group {}".format(name, c2g[name].name)
            gateable_clocks.add_constant(clock_name, docstring)
        gateable_clocks.add_number_of_variants("Number of Valid Gateable Clock")

        for name in by_type.hint_clks.keys():
            # All these clocks start with `clk_` which is redundant.
            clock_name = Name.from_snake_case(name).remove_part("clk")
            docstring = "Clock {} in group {}".format(name, c2g[name].name)
            hintable_clocks.add_constant(clock_name, docstring)
        hintable_clocks.add_number_of_variants("Number of Valid Hintable Clock")

        self.clkmgr_gateable_clocks = gateable_clocks
        self.clkmgr_hintable_clocks = hintable_clocks

    def _init_mmio_region(self):
        """
        Computes the bounds of the MMIO region.

        MMIO region excludes any memory that is separate from the module configuration
        space, i.e. ROM, main SRAM, and flash are excluded but retention SRAM,
        spi_device memory, or usbdev memory are included.
        """
        memories = [region.base_addr for (_, region) in self.memories()]
        # TODO(#14345): Remove the hardcoded "rv_dm" name check below.
        regions = [
            region for ((dev_name, _), region) in self.devices()
            if region.base_addr not in memories and dev_name != "rv_dm"
        ]
        # Note: The memory interface of the retention RAM is in the MMIO address space,
        # which we prefer since it reduces the number of ePMP regions we need.
        mmio = range(min([r.base_addr for r in regions]),
                     max([r.base_addr + r.size_bytes for r in regions]))
        self.mmio = MemoryRegion(self._top_name, Name(["mmio"]), mmio.start,
                                 mmio.stop - mmio.start)
