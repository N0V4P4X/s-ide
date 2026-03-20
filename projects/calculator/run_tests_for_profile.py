import unittest
import sys

if __name__ == '__main__':
    sys.argv = ['unittest', 'discover', 'test/']
    unittest.main(module=None)
