#!/usr/bin/env python3
# This file is part of Xpra.
# Copyright (C) 2018-2020 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import unittest

from xpra.os_util import DummyContextManager
from xpra.util import AdHocStruct
from xpra.client.mixins.mmap import MmapClient, log

from unit.test_util import silence_info, silence_error
from unit.client.mixins.clientmixintest_util import ClientMixinTest


class MixinsTest(ClientMixinTest):

	def test_mmap(self):
		class badfile:
			def close(self):
				raise Exception("test close failure handling")
		import tempfile
		tmp_dir = tempfile.gettempdir()
		for mmap, ctx in {
			"off" : DummyContextManager(),
			"on"  : silence_info(log),
			tmp_dir+"/xpra-mmap-test-file-%i" % os.getpid() : silence_info(log),
			tmp_dir+"/xpra-fail-mmap-test-file-%i" % os.getpid() : silence_error(log),
			}.items():
			opts = AdHocStruct()
			opts.mmap = mmap
			opts.mmap_group = False
			with ctx:
				m = self._test_mixin_class(MmapClient, opts, {
					"mmap.enabled"		: True,
					})
			fail = bool(m.mmap_filename) and m.mmap_filename.find("fail")>=0
			assert m.mmap_enabled == (mmap!="off" and not fail)
			assert len(self.exit_codes)==int(fail)
			m.cleanup()
			#no-op:
			m.cleanup()
			m.mmap_tempfile = badfile()
			m.cleanup()

	def make_caps(self, caps=None):
		d = super().make_caps(caps)
		x = self.mixin
		d.update({
			"mmap_enabled"		: True,
			"mmap.token"		: x.mmap_token,
			"mmap.token_bytes"	: x.mmap_token_bytes,
			"mmap.token_index"	: x.mmap_token_index,
			})
		if x.mmap_filename and x.mmap_filename.find("fail")>=0:
			d["mmap.token_index"] = x.mmap_token_index-10
		return d


def main():
	unittest.main()


if __name__ == '__main__':
	main()
