import unittest

from core.vision import crop_bounds, pixel_to_latlon


class CropBoundsTests(unittest.TestCase):
    def test_centers_crop_when_inside_image(self):
        self.assertEqual(crop_bounds(50, 60, img_w=120, img_h=140, crop_size=20), (40, 50, 60, 70))

    def test_clamps_crop_to_image_edges(self):
        self.assertEqual(crop_bounds(3, 4, img_w=100, img_h=80, crop_size=20), (0, 0, 20, 20))
        self.assertEqual(crop_bounds(98, 78, img_w=100, img_h=80, crop_size=20), (80, 60, 100, 80))

    def test_caps_crop_size_to_shorter_image_dimension(self):
        self.assertEqual(crop_bounds(50, 50, img_w=80, img_h=60, crop_size=100), (20, 0, 80, 60))


class PixelToLatLonTests(unittest.TestCase):
    def test_maps_pixel_coordinates_to_bbox(self):
        metadata = {
            "bbox_4326": {
                "min_lat": 51.0,
                "max_lat": 52.0,
                "min_lon": 17.0,
                "max_lon": 19.0,
            }
        }

        lat, lon = pixel_to_latlon(50, 25, metadata, img_w=100, img_h=100)

        self.assertAlmostEqual(lat, 51.75)
        self.assertAlmostEqual(lon, 18.0)


if __name__ == "__main__":
    unittest.main()
