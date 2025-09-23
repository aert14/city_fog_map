import unittest
import sys
import os

# Add the parent directory to the Python path to allow importing 'app'
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import utils

class TestUtils(unittest.TestCase):

    def test_radius_to_h3_resolution(self):
        self.assertEqual(utils.radius_to_h3_resolution(10), 13)
        self.assertEqual(utils.radius_to_h3_resolution(30), 13)
        self.assertEqual(utils.radius_to_h3_resolution(31), 12)
        self.assertEqual(utils.radius_to_h3_resolution(70), 12)
        self.assertEqual(utils.radius_to_h3_resolution(71), 11)
        self.assertEqual(utils.radius_to_h3_resolution(150), 11)
        self.assertEqual(utils.radius_to_h3_resolution(151), 10)
        self.assertEqual(utils.radius_to_h3_resolution(1000), 10)

if __name__ == '__main__':
    unittest.main()
