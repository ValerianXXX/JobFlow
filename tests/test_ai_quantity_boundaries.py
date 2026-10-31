import unittest

import _support  # noqa: F401
from jobops.ai_runtime import _statement_grounding_report


class AIQuantityBoundaryTests(unittest.TestCase):
    def test_explicit_currency_code_before_a_symbol_is_not_discarded(self):
        source = 'The team managed USD $5000 for the synthetic pilot.'
        candidate = 'The team managed CAD $5000 for the synthetic pilot.'
        self.assertFalse(_statement_grounding_report(candidate, source)['valid'])

    def test_explicit_currency_code_can_preserve_a_redundant_symbol(self):
        source = 'The team managed USD $5000 for the synthetic pilot.'
        candidate = 'The team managed USD 5000 for the synthetic pilot.'
        self.assertTrue(_statement_grounding_report(candidate, source)['valid'])

    def test_long_source_prefixes_do_not_change_local_quantity_identity(self):
        supported = 'The applicant measured 20 milliseconds in the synthetic benchmark.'
        unsupported = 'The applicant measured 20 seconds in the synthetic benchmark.'
        source = ('An unrelated synthetic sentence has no quantity. ' * 2000) + supported
        self.assertTrue(_statement_grounding_report(supported, source)['valid'])
        self.assertFalse(_statement_grounding_report(unsupported, source)['valid'])


if __name__ == '__main__':
    unittest.main()
