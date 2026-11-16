import unittest

from app import greet


class GreetTests(unittest.TestCase):
	def testDefault(self):
		self.assertEqual(greet(), "Hello, world!")


if __name__ == "__main__":
	unittest.main()
