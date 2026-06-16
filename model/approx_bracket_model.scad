
// Approximate bracket model reconstructed from photos.
// Dimensions are ESTIMATES and must be measured from the real part.

th = 3;          // material thickness
base_w = 80;
base_d = 60;
wall_h = 40;

difference() {
    union() {
        // base
        cube([base_w, base_d, th]);

        // back wall
        translate([0,0,th])
            cube([base_w, th, wall_h]);

        // side tabs
        translate([0,10,th])
            cube([th,20,25]);

        translate([base_w-th,10,th])
            cube([th,20,25]);

        // top ears
        translate([0,0,wall_h+th])
            rotate([0,90,0]) cube([th,15,20]);

        translate([base_w-20,0,wall_h+th])
            rotate([0,90,0]) cube([th,15,20]);
    }

    // large holes (approximate)
    translate([25,30,-1]) cylinder(h=10,d=12,$fn=64);
    translate([55,30,-1]) cylinder(h=10,d=12,$fn=64);

    translate([25,1,25]) rotate([90,0,0])
        cylinder(h=10,d=12,$fn=64);
    translate([55,1,25]) rotate([90,0,0])
        cylinder(h=10,d=12,$fn=64);
}
